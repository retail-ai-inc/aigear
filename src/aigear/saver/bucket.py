from google.cloud import storage


class GCPStorage:
    def __init__(self, cfg):
        self.storage_client = storage.Client(cfg["project_id"])

    def _get_file_from_bucket(self, download_file_name):
        bucket = self.storage_client.get_bucket(self.cfg["data_bucket_name"])
        blob = bucket.blob(download_file_name)
        blob.download_to_filename(download_file_name)
        return True

    def _write_file_to_bucket(self, write_file_name):
        bucket = self.storage_client.get_bucket(self.cfg["model_bucket_name"])
        blob = bucket.blob(f'model/{write_file_name}.mdl')
        blob.cache_control = 'no-cache'
        blob.upload_from_filename(f'{write_file_name}.mdl')

        prod_bucket = self.storage_client.get_bucket(self.cfg["production_bucket_name"])
        blob = prod_bucket.blob(f'/2/fm/{write_file_name}.mdl')
        blob.cache_control = 'no-cache'
        blob.upload_from_filename(f'{write_file_name}.mdl')
        return True
