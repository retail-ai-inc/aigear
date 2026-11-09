from copy import deepcopy

from aigear.common import run_sh
from aigear.common.logger import Logging
from aigear.db.bucket import RealGCS
from aigear.management.asset_errors import IntegrityError, SnapshotConflict

logger = Logging(log_name=__name__).console_logging()


def trash_rule(days):
    if type(days) is not int or days <= 0:
        raise ValueError('trash_retention_days must be a positive integer')
    return {'action': {'type': 'Delete'}, 'condition': {'matchesPrefix': ['trash/'], 'daysSinceCustomTime': days}}


def merge_trash_lifecycle(rules, days):
    """Own only the exact trash/ + daysSinceCustomTime Delete rule shape."""
    desired = trash_rule(days)
    if not isinstance(rules, list) or any(not isinstance(rule, dict) for rule in rules):
        raise IntegrityError('Invalid bucket lifecycle rule list', phase='lifecycle')
    result = deepcopy(rules)
    owned = []
    for index, rule in enumerate(result):
        action, condition = rule.get('action', {}), rule.get('condition', {})
        if not isinstance(action, dict) or not isinstance(condition, dict):
            raise IntegrityError('Invalid bucket lifecycle rule', phase='lifecycle')
        if action.get('type') != 'Delete':
            continue
        if (set(rule) == {'action', 'condition'} and action == {'type': 'Delete'}
                and set(condition) == {'matchesPrefix', 'daysSinceCustomTime'}
                and condition['matchesPrefix'] == ['trash/'] and type(condition['daysSinceCustomTime']) is int
                and condition['daysSinceCustomTime'] >= 0):
            owned.append(index)
            continue
        prefixes = condition.get('matchesPrefix')
        if (not isinstance(prefixes, list) or not prefixes or any(not isinstance(prefix, str) for prefix in prefixes)
                or any('trash/'.startswith(prefix) or prefix.startswith('trash/') for prefix in prefixes)):
            raise SnapshotConflict('An existing Delete rule overlaps managed trash', phase='lifecycle')
    if len(owned) > 1:
        raise SnapshotConflict('Multiple managed trash lifecycle rules are ambiguous', phase='lifecycle')
    if owned:
        result[owned[0]] = desired
    else:
        result.append(desired)
    return result


class Bucket:
    def __init__(
        self,
        bucket_name: str,
        location: str,
        project_id: str,
    ):
        self.bucket_gs = f"gs://{bucket_name}"
        self.bucket_name = bucket_name
        self.location = location
        self.project_id = project_id

    def ensure_trash_lifecycle(self, days):
        if days is None:
            return None
        trash_rule(days)
        client = RealGCS(self.project_id, self.bucket_name)
        try:
            current = client.get_lifecycle()
            rules = merge_trash_lifecycle(current.rules, days)
            if rules == current.rules:
                logger.info(f'Trash lifecycle already synchronized: {self.bucket_gs}, {days} days since Custom-Time')
                return current
            result = client.update_lifecycle(rules, if_metageneration_match=current.metageneration)
            logger.info(f'Trash lifecycle synchronized: {self.bucket_gs}, {days} days since Custom-Time')
            return result
        finally:
            client.close()

    def trash_lifecycle_status(self, days):
        if days is None:
            return 'EXISTS [trash lifecycle disabled]'
        trash_rule(days)
        client = RealGCS(self.project_id, self.bucket_name)
        try:
            current = client.get_lifecycle()
            expected = merge_trash_lifecycle(current.rules, days)
            return (f'EXISTS [trash lifecycle: {days} days since Custom-Time]' if expected == current.rules
                    else f'PARTIAL [trash lifecycle differs; expected {days} days since Custom-Time]')
        finally:
            client.close()

    def create(self):
        command = [
            "gcloud",
            "storage",
            "buckets",
            "create",
            self.bucket_gs,
            f"--location={self.location}",
            "--uniform-bucket-level-access",
            f"--project={self.project_id}",
        ]
        run_sh(command, check=True)

    def add_permissions_to_gcs(self, sa_email):
        command = [
            "gcloud",
            "storage",
            "buckets",
            "add-iam-policy-binding",
            self.bucket_gs,
            f"--member=serviceAccount:{sa_email}",
            "--role=roles/storage.admin",
            f"--project={self.project_id}",
        ]
        run_sh(command, check=True)

    def describe(self):
        command = [
            "gcloud",
            "storage",
            "buckets",
            "describe",
            self.bucket_gs,
            f"--project={self.project_id}",
        ]
        try:
            event = run_sh(command, check=True)
        except RuntimeError as exc:
            if any(
                marker in str(exc)
                for marker in ("BucketNotFoundException", "NOT_FOUND", "not found")
            ):
                return False
            raise
        if self.bucket_gs not in event:
            raise RuntimeError(f"Unexpected bucket describe output: {event}")
        return True

    def list(self):
        command = [
            "gcloud",
            "storage",
            "buckets",
            "list",
            self.bucket_gs,
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        logger.info(f"\n{event}")

    def delete(self):
        command = [
            "gcloud",
            "storage",
            "rm",
            "-r",
            self.bucket_gs,
            f"--project={self.project_id}",
        ]
        event = run_sh(command, check=True)
        logger.info(event)
