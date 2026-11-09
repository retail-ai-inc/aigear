"""An in-memory HTTP/SDK harness; no local-store implementation is reused.

Real Blob reload/patch/rewrite/delete build their SDK requests against this
service. Media transport and listing are replaced to avoid network traffic.
"""
import copy
from urllib.parse import unquote

import pytest
from google.api_core import exceptions
from google.cloud import storage

from aigear.db.bucket import RealGCS, LocalGCSMock
from aigear.db.storage_common import crc32c


class GCSService:
    def __init__(self, monkeypatch):
        self.objects = {}
        self.buckets = {}
        self.sequence = 100
        self.requests = []
        self.client = storage.Client.create_anonymous_client()
        monkeypatch.setattr(storage, "Client", lambda project: self.client)
        monkeypatch.setattr(self.client, "_get_resource", self.get)
        monkeypatch.setattr(self.client, "_post_resource", self.post)
        monkeypatch.setattr(self.client, "_patch_resource", self.patch)
        monkeypatch.setattr(self.client, "_delete_resource", self.delete)
        monkeypatch.setattr(storage.Blob, "upload_from_string", self.upload_method())
        monkeypatch.setattr(storage.Blob, "download_as_bytes", self.download_method())
        monkeypatch.setattr(storage.Bucket, "list_blobs", self.list_method())

    @staticmethod
    def key(path):
        parts = path.split("/o/", 1)
        return parts[0].split("/b/")[1], unquote(parts[1])

    def bucket(self, name):
        return self.buckets.setdefault(name, {"name": name, "metageneration": "1", "lifecycle": {"rule": []}})

    def object(self, key):
        if key not in self.objects:
            raise exceptions.NotFound("missing")
        return self.objects[key]

    @staticmethod
    def condition(properties, query, prefix=""):
        for field in ("generation", "metageneration"):
            parameter = "if" + prefix + field[0].upper() + field[1:] + "Match"
            if parameter in query:
                actual = properties[field] if properties else "0"
                if str(query[parameter]) != str(actual):
                    raise exceptions.PreconditionFailed("changed")

    def get(self, path, query_params=None, **kwargs):
        self.requests.append(("get", path, copy.deepcopy(query_params), None))
        if "/o/" not in path:
            return copy.deepcopy(self.bucket(path.split("/b/")[1]))
        properties, _ = self.object(self.key(path))
        self.condition(properties, query_params or {})
        return copy.deepcopy(properties)

    def put(self, key, data, properties, query):
        old = self.objects.get(key)
        self.condition(old[0] if old else None, query)
        self.sequence += 1
        properties = copy.deepcopy(properties)
        properties.update(name=key[1], bucket=key[0], generation=str(self.sequence),
                          metageneration="1", size=str(len(data)), crc32c=crc32c(data))
        self.objects[key] = properties, data
        return copy.deepcopy(properties)

    def upload_method(self):
        def upload(blob, data, content_type=None, if_generation_match=None, **kwargs):
            query = {} if if_generation_match is None else {"ifGenerationMatch": if_generation_match}
            properties = dict(blob._properties, contentType=content_type)
            blob._set_properties(self.put((blob.bucket.name, blob.name), data, properties, query))
        return upload

    def download_method(self):
        def download(blob, if_generation_match=None, **kwargs):
            properties, data = self.object((blob.bucket.name, blob.name))
            self.condition(properties, {"ifGenerationMatch": if_generation_match})
            return data
        return download

    def list_method(self):
        def listing(bucket, prefix=""):
            result = []
            for (bucket_name, name), (properties, _) in self.objects.items():
                if bucket_name == bucket.name and name.startswith(prefix):
                    blob = bucket.blob(name)
                    blob._set_properties(copy.deepcopy(properties))
                    result.append(blob)
            return result
        return listing

    def post(self, path, data, query_params=None, **kwargs):
        self.requests.append(("post", path, copy.deepcopy(query_params), copy.deepcopy(data)))
        source_path, target_path = path.split("/rewriteTo")
        properties, content = self.object(self.key(source_path))
        self.condition(properties, query_params or {}, "Source")
        target = self.put(self.key(target_path), content, data, query_params or {})
        return {"done": True, "totalBytesRewritten": str(len(content)),
                "objectSize": str(len(content)), "resource": target}

    def patch(self, path, data, query_params=None, **kwargs):
        self.requests.append(("patch", path, copy.deepcopy(query_params), copy.deepcopy(data)))
        properties = self.object(self.key(path))[0] if "/o/" in path else self.bucket(path.split("/b/")[1])
        self.condition(properties, query_params or {})
        properties.update(copy.deepcopy(data))
        properties["metageneration"] = str(int(properties["metageneration"]) + 1)
        return copy.deepcopy(properties)

    def delete(self, path, query_params=None, **kwargs):
        self.requests.append(("delete", path, copy.deepcopy(query_params), None))
        properties, _ = self.object(self.key(path))
        self.condition(properties, query_params or {})
        del self.objects[self.key(path)]


@pytest.fixture(params=["local", "gcs"])
def stores(request, tmp_path, monkeypatch):
    if request.param == "local":
        return (LocalGCSMock("p", tmp_path / "source"),
                LocalGCSMock("p", tmp_path / "destination"))
    service = GCSService(monkeypatch)
    return RealGCS("p", "source"), RealGCS("p", "destination")


@pytest.fixture
def gcs_service(monkeypatch):
    return GCSService(monkeypatch)
