import pickle
from sentry_sdk import capture_exception


class Iris(object):
    def __init__(self, model_path):
        self.model_path = model_path
        # load model
        self.model = self.load_pickle()

    def load_pickle(self):
        try:
            model = self.load_model()
        except Exception:
            model = self.load_model()
        return model

    def predict(self, data) -> float:
        result = -1
        try:
            feature = [data["features"]]
            result = self.model.predict(feature)[0]
        except Exception as e:
            capture_exception(e)
        return float(result)

    def load_model(self):
        with open(self.model_path, "rb") as pickle_file:
            model = pickle.load(pickle_file)
        return model
