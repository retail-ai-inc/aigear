from sklearn.datasets import load_iris
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC
from sklearn.metrics import classification_report, accuracy_score
from aigear.pipeline import Pipeline, task
import time


@task
def load_data():
    iris = load_iris()
    return iris


@task
def split_dataset(iris):
    X_train, X_test, y_train, y_test = train_test_split(iris.data, iris.target, test_size=0.2, random_state=42)
    return X_train, X_test, y_train, y_test


@task
def feature_process(X_train, X_test):
    scaler = StandardScaler()
    X_train = scaler.fit_transform(X_train)
    X_test = scaler.transform(X_test)
    return X_train, X_test


@task
def fit_model(X_train, y_train):
    svm_classifier = SVC(kernel='linear', random_state=42)
    svm_classifier.fit(X_train, y_train)
    return svm_classifier


@task
def evaluate(svm_classifier, X_test, y_test):
    y_pred = svm_classifier.predict(X_test)
    accuracy = accuracy_score(y_test, y_pred)
    # accuracy.get("ss")
    return y_pred, accuracy


def my_pipeline():
    pipeline = Pipeline(
        name="template",
        version="0.0.1",
        describe="",
    )

    # Dependency relationship
    step1 = pipeline.step(load_data)
    step2 = pipeline.step(
        split_dataset,
        step1.get_output(),
        ('X_train', 'X_test', 'y_train', 'y_test')
    )
    step3 = pipeline.step(
        feature_process,
        step2.get_output('X_train', 'X_test'),
        ('X_train', 'X_test')
    )
    step4 = pipeline.step(
        fit_model,
        (step3.get_output('X_train'), step2.get_output('y_train')),
    )
    step5 = pipeline.step(
        evaluate,
        (
            step4.get_output(),
            step3.get_output('X_test'),
            step2.get_output('y_test')
        ),
        ('y_pred', 'accuracy')
    )
    print(step5)
    print('-------------------------------------------------------')

    # Operation mode
    my_workflow = pipeline.workflow(
        step1,
        step2,
        step3,
        step4,
        step5,
    )
    print(my_workflow)

    print("------start------")
    start_time = time.time()

    result = pipeline.run()
    end_time = time.time()
    print('Pipeline run time: ', round(end_time - start_time, 3), 's')

    print("准确率：", result.get("evaluate")['accuracy'])
    y_test = result.get("split_dataset")['y_test']
    y_pred = result.get("evaluate")['y_pred']
    iris = result.get("load_data")

    print("分类报告：\n", classification_report(y_test, y_pred, target_names=iris.target_names))
    print("------end------")


if __name__ == "__main__":
    my_pipeline()
