# This script is used to generate the model
import pickle
from sklearn.model_selection import train_test_split
from sklearn.linear_model import LogisticRegression
from sklearn.datasets import load_iris
from sklearn import metrics
import pandas as pd


def model_feature():
    data = load_iris()
    iris_target = data.target
    iris_features = pd.DataFrame(data=data.data, columns=data.feature_names)
    return iris_target, iris_features


def main():
    model_path = "iris_model.pkl"
    iris_target, iris_features = model_feature()
    x_train, x_test, y_train, y_test = train_test_split(iris_features, iris_target, test_size=0.2,
                                                        random_state=2023)

    clf = LogisticRegression(random_state=0, max_iter=1000, solver="lbfgs")
    model = clf.fit(x_train, y_train)
    with open(model_path, "wb") as md:
        pickle.dump(model, md)

    test_predict = clf.predict(x_test)
    acc = metrics.accuracy_score(y_test, test_predict)
    return acc


if __name__ == "__main__":
    acc = main()
    print('acc: ', acc)
