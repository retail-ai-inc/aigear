import logging
import json
import sys
from google.cloud.logging_v2.handlers import StructuredLogHandler


class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            'process': record.process,
            'timestamp': self.formatTime(record),
            'level': record.levelname,
            'message': record.getMessage(),
        }
        return json.dumps(log_data)


class Logging:
    def __init__(
        self,
        log_name: str = 'medovik_logging',
        project_id: str = None
    ):
        self.client = None
        self.project_id = project_id
        self.log_name = log_name

    def root_logger(self):
        logger = logging.getLogger(self.log_name)
        logger.setLevel(logging.INFO)
        return logger

    def gcp_logging_handler(self):
        handler = StructuredLogHandler(project_id=self.project_id)
        return handler

    def cloud_logging(self):
        logger = self.root_logger()
        console_handler = self.console_logging_handler()
        gcp_handler = self.gcp_logging_handler()
        logger.addHandler(console_handler)
        logger.addHandler(gcp_handler)
        return logger

    @staticmethod
    def console_logging_handler():
        handler = logging.StreamHandler(sys.stdout)
        formatter = JsonFormatter()
        handler.setFormatter(formatter)
        return handler

    def console_logging(self):
        logger = self.root_logger()
        console_handler = self.console_logging_handler()
        logger.addHandler(console_handler)
        return logger
