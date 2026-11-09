"""GCS implementation of the internal versioned bucket protocol."""
from __future__ import annotations

import weakref
from pathlib import Path

from google.cloud import storage
from google.api_core import exceptions as gcs_errors

from aigear.common.schema.bucket_schema import (
    BucketABC, BucketLifecycle, ObjectMetadata, ObjectNotFound, VersionMismatch,
)
from aigear.db.storage_common import atomic_file, check_version, storage_call, utc, version
from aigear.management.asset_paths import relative_path


def _metadata(blob):
    return ObjectMetadata(blob.name, str(blob.generation), str(blob.metageneration),
                          int(blob.size), blob.crc32c, utc(blob.custom_time),
                          dict(blob.metadata or {}), blob.content_type)


class RealGCS(BucketABC):
    def __init__(self, project_id, bucket_name):
        super().__init__(project_id, bucket_name)
        with storage_call("connect"):
            self.bucket_client = storage.Client(project_id)
            self.bucket = self.bucket_client.get_bucket(bucket_name)
        weakref.finalize(self, self.bucket_client.close)

    def close(self):
        if hasattr(self, "bucket_client"):
            self.bucket_client.close()

    def get_metadata(self, name):
        with storage_call("metadata"):
            blob = self.bucket.blob(relative_path(name))
            blob.reload()
            return _metadata(blob)

    def list_metadata(self, prefix=""):
        if prefix:
            relative_path(prefix)
        with storage_call("list"):
            return sorted((_metadata(b) for b in self.bucket.list_blobs(prefix=prefix)),
                          key=lambda m: m.name)

    def read_bytes(self, name, *, generation=None):
        generation = version(generation)
        name = relative_path(name)
        with storage_call("read"):
            current = self.get_metadata(name)
            check_version(current.generation, generation, fixed=True)
            try:
                return self.bucket.blob(name).download_as_bytes(
                    if_generation_match=int(generation or current.generation), checksum="crc32c")
            except gcs_errors.PreconditionFailed as exc:
                raise VersionMismatch("Object changed during fixed read", phase="read", cause=exc) from exc

    def download(self, bucket_blob_name, local_blob_path, *, generation=None):
        data = self.read_bytes(bucket_blob_name, generation=generation)
        with storage_call("download"):
            atomic_file(Path(local_blob_path), data)

    def write_bytes(self, name, data, *, if_generation_match=None,
                    custom_time=None, metadata=None, content_type=None):
        condition = version(if_generation_match, zero=True)
        custom_time = utc(custom_time)
        with storage_call("upload"):
            blob = self.bucket.blob(relative_path(name))
            blob.cache_control = "no-cache"
            blob.metadata = dict(metadata or {})
            if custom_time is not None:
                blob.custom_time = custom_time
            blob.upload_from_string(data, content_type=content_type or "application/octet-stream",
                                    if_generation_match=None if condition is None else int(condition),
                                    checksum="crc32c")
            return _metadata(blob)

    def upload(self, local_blob_name, bucket_blob_name, *, if_generation_match=None,
               custom_time=None, metadata=None, content_type=None):
        with storage_call("upload"):
            data = Path(local_blob_name).read_bytes()
            return self.write_bytes(bucket_blob_name, data, if_generation_match=if_generation_match,
                                    custom_time=custom_time, metadata=metadata, content_type=content_type)

    def copy_blob(self, source_blob_name, destination_blob_name, *, destination_bucket=None,
                  source_generation=None, if_generation_match=None, custom_time=None):
        destination = self if destination_bucket is None else destination_bucket
        if not isinstance(destination, RealGCS):
            raise TypeError("Copy requires two GCS buckets")
        source_name = relative_path(source_blob_name)
        target_name = relative_path(destination_blob_name)
        source_generation = version(source_generation)
        condition = version(if_generation_match, zero=True)
        custom_time = utc(custom_time)
        with storage_call("copy"):
            source_info = self.get_metadata(source_name)
            check_version(source_info.generation, source_generation, fixed=True)
            if condition is None:
                try:
                    condition = destination.get_metadata(target_name).generation
                except ObjectNotFound:
                    condition = "0"
            # An unversioned source plus sourceGenerationMatch checks the live
            # object even when the bucket retains historical generations.
            source = self.bucket.blob(source_name)
            target = destination.bucket.blob(target_name)
            # Nonempty replacement metadata prevents editable metadata inheritance,
            # including Custom-Time. Never send customTime:null to clear it.
            target.metadata = source_info.metadata
            target.content_type = source_info.content_type or "application/octet-stream"
            target.cache_control = "no-cache"
            if custom_time is not None:
                target.custom_time = custom_time
            token = None
            while True:
                token, _, _ = target.rewrite(
                    source, token=token, if_generation_match=int(condition),
                    if_source_generation_match=int(source_info.generation),
                    if_source_metageneration_match=int(source_info.metageneration))
                if token is None:
                    return _metadata(target)

    def delete(self, name, *, if_generation_match):
        condition = version(if_generation_match, optional=False)
        with storage_call("delete"):
            self.bucket.blob(relative_path(name)).delete(if_generation_match=int(condition))

    def update_metadata(self, name, *, if_generation_match, if_metageneration_match,
                        custom_time=None, metadata=None):
        generation = version(if_generation_match, optional=False)
        metageneration = version(if_metageneration_match, optional=False)
        custom_time = utc(custom_time)
        with storage_call("patch"):
            name = relative_path(name)
            current = self.bucket.blob(name)
            current.reload(if_generation_match=int(generation), if_metageneration_match=int(metageneration))
            # reload populates Blob.generation, which the SDK would then send as
            # a historical version selector. Patch the live name instead.
            blob = self.bucket.blob(name)
            if custom_time is not None:
                if current.custom_time is not None and custom_time < utc(current.custom_time):
                    raise ValueError("Custom-Time cannot move backwards; use rewrite")
                blob.custom_time = custom_time
            if metadata is not None:
                blob.metadata = dict(metadata)
            blob.patch(if_generation_match=int(generation), if_metageneration_match=int(metageneration))
            return _metadata(blob)

    def get_lifecycle(self):
        with storage_call("lifecycle_read"):
            current = self.bucket_client.bucket(self.bucket_name)
            current.reload()
            return BucketLifecycle(str(current.metageneration), list(current.lifecycle_rules))

    def update_lifecycle(self, rules, *, if_metageneration_match):
        condition = version(if_metageneration_match, optional=False)
        with storage_call("lifecycle_update"):
            # A fresh SDK bucket avoids retaining unrelated dirty properties.
            current = self.bucket_client.bucket(self.bucket_name)
            current.lifecycle_rules = rules
            current.patch(if_metageneration_match=int(condition))
            return BucketLifecycle(str(current.metageneration), list(current.lifecycle_rules))


from aigear.db.local_bucket import LocalGCSMock  # Preserve the existing import path.


def bucket_client(project_id: str | None = None, bucket_name: str | None = None,
                  bucket_on: bool = True):
    return RealGCS(project_id, bucket_name) if bucket_on else LocalGCSMock(project_id, bucket_name)
