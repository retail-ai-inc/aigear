import cloudpickle
import os


class SaveModel:
    def __init__(self):
        self.folder_name = ""

    def save_location(self):
        if not os.path.exists(folder_name):
            os.makedirs(folder_name)

    @staticmethod
    def save(model=None, model_file=None):
        if model_file == None:
            pass
        with open(model_file, "wb") as pickle_file:
            cloudpickle.dump(model, pickle_file)

    @staticmethod
    def load(model_file=None):
        with open(model_file, "rb") as pickle_file:
            self._model = pickle.load(pickle_file)
