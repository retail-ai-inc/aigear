# Aigear
Aigear is an MLOps work orchestration framework that is serverless and aims to help you build pipelines more quickly.
It will not change the programming style of Python and provides an optimized out-of-the-box toolbox.

Aigear believes that everything is a task, and it allows you to make any extensions according to the task.
Within Aigear, it performs topology analysis on tasks to achieve optimal parallel execution.

You need to note that aigear only executes functions created by yourself in parallel.

## Getting started

Aigear requires Python 3.8 or later. To install the latest or upgrade to the latest version of Aigear, run the following command:

```bash
pip install -U aigear
```

With just two decorators(`@task` and `@workflow`), you can create a pipeline.
Here is an example of iris classification:
```python
from sklearn.datasets import load_iris
from sklearn.model_selection import train_test_split
from aigear.pipeline import workflow, task


@task
def load_data():
    iris = load_iris()
    return iris


@task
def split_dataset(iris):
    X_train, X_test, y_train, y_test = train_test_split(iris.data, iris.target, test_size=0.2, random_state=42)
    return X_train, X_test, y_train, y_test


@workflow
def my_pipeline():
    iris = load_data()
    X_train, X_test, y_train, y_test = split_dataset(iris)
    print("X_train len: ", len(X_train))

if __name__ == '__main__':
    # # Run the pipeline directly
    # my_pipeline()
    
    # Run pipeline in parallel
    my_pipeline.run_in_executor()
```

Similarly, the task can also be executed in two ways.
```python
from sklearn.datasets import load_iris
from aigear.pipeline import task

@task
def load_data():
    iris = load_iris()
    return iris

if __name__ == "__main__":
    # # Run the pipeline directly
    # load_data()
    
    # Run pipeline in parallel
    load_data.run_in_executor()
```

If you want to deploy a pipeline or create a model service in local docker, it's quite simple.
Please refer to `/aigear/example/pipeline`

```python
from sklearn.datasets import load_iris
from sklearn.model_selection import train_test_split
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
from aigear.pipeline import workflow, task
import pickle
import json
import os


@task
def load_data():
    iris = load_iris()
    return iris


@task
def split_dataset(iris):
    X_train, X_test, y_train, y_test = train_test_split(iris.data, iris.target, test_size=0.2, random_state=42)
    return X_train, X_test, y_train, y_test


@task
def fit_model(X_train, y_train):
    clf = LogisticRegression(random_state=0, max_iter=1000, solver="lbfgs")
    model = clf.fit(X_train, y_train)
    return model


@task
def evaluate(clf, X_test, y_test):
    y_pred = clf.predict(X_test)
    accuracy = accuracy_score(y_test, y_pred)
    return y_pred, accuracy


@task
def get_env_variables():
    with open("env.json", "r") as f:
        env = json.load(f)
    model_path = env.get("grpc", {}).get("servers", {}).get("demo", {}).get("modelPath")
    return model_path


@task
def save_model(model, model_path):
    with open(model_path, "wb") as md:
        pickle.dump(model, md)


@workflow
def my_pipeline():
    iris = load_data()
    X_train, X_test, y_train, y_test = split_dataset(iris)
    model = fit_model(X_train, y_train)
    y_pred, accuracy = evaluate(model, X_test, y_test)
    print("准确率：", accuracy)

    model_path = get_env_variables()
    save_model(model, model_path)


if __name__ == '__main__':
    current_directory = os.getcwd()
    volumes = {
        current_directory: {'bind': "/pipeline", 'mode': 'rw'}
    }
    hostname = "demo-host"
    ports = {'50051/tcp': 50051}
    service_dir = "demo"

    my_pipeline.deploy(
        volumes=volumes,
        skip_build_image=False
    ).to_service(
        hostname=hostname,
        ports=ports,
        volumes=volumes,
        tag=service_dir,
    )
```


