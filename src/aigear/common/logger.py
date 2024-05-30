import logging
import sys
# import json


# # TODO: Google Cloud is unable to capture JSON format logs. Can only capture messages
# class JsonFormatter(logging.Formatter):
#     def format(self, record):
#         log_data = {
#             'process': record.process,
#             'timestamp': self.formatTime(record),
#             'level': record.levelname,
#             'message': record.getMessage(),
#         }
#         return json.dumps(log_data)


def init_logger():
    logger_instance = logging.getLogger(__name__)
    handler = logging.StreamHandler(sys.stdout)
    # formatter = JsonFormatter()
    formatter = logging.Formatter('%(process)d - %(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger_instance.addHandler(handler)
    logger_instance.setLevel(logging.INFO)
    return logger_instance


# Global logger
logger = init_logger()
