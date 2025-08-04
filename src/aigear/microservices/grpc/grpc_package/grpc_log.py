import logging
import sys
import json


class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            'process': record.process,
            'timestamp': self.formatTime(record),
            'level': record.levelname,
            'message': record.getMessage(),
        }
        return json.dumps(log_data)


class GrpcLog:
    def grpc_log(self):
        """
        Record the log of gRPC.
        
        Return logger 
        """
        logger = logging.getLogger(__name__)
        # logger setting
        handler = logging.StreamHandler(sys.stdout)
        formatter = JsonFormatter()
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        return logger
