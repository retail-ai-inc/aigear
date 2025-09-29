from sklearn.datasets import load_iris
from sklearn.model_selection import train_test_split
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
from aigear.pipeline import workflow, task
from aigear.common import create_stage_logger, PipelineStage
import pickle
import json
import os


# Create stage-aware loggers
training_logger = create_stage_logger(
    stage=PipelineStage.TRAINING,
    module_name=__name__,
    cpu_count=2,
    memory_limit="2GB",
    gpu_enabled=False
)

inference_logger = create_stage_logger(
    stage=PipelineStage.INFERENCE,
    module_name=__name__,
    cpu_count=1,
    memory_limit="1GB",
    gpu_enabled=False
)


@task
def load_data():
    with training_logger.stage_context() as logger:
        logger.info("Loading Iris dataset")
        iris = load_iris()
        logger.info(f"Dataset loaded: {iris.data.shape[0]} samples, {iris.data.shape[1]} features")
        return iris


@task
def split_dataset(iris):
    with training_logger.stage_context() as logger:
        logger.info("Splitting dataset into train/test")
        x_train, x_test, y_train, y_test = train_test_split(iris.data, iris.target, test_size=0.2, random_state=42)
        logger.info(f"Train set: {x_train.shape[0]} samples, Test set: {x_test.shape[0]} samples")
        return x_train, x_test, y_train, y_test


@task
def fit_model(x_train, y_train):
    with training_logger.stage_context() as logger:
        logger.info("Starting model training")
        clf = LogisticRegression(random_state=0, max_iter=1000, solver="lbfgs")
        model = clf.fit(x_train, y_train)
        logger.info("Model training completed")
        return model


@task
def evaluate(clf, x_test, y_test):
    with inference_logger.stage_context() as logger:
        logger.info("Starting model evaluation")
        y_pred = clf.predict(x_test)
        accuracy = accuracy_score(y_test, y_pred)
        logger.log_prediction(
            latency_ms=1.0,  # 模拟预测时间
            batch_size=len(x_test),
            confidence=accuracy
        )
        logger.info(f"Model accuracy: {accuracy:.4f}")
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
    x_train, x_test, y_train, y_test = split_dataset(iris)
    model = fit_model(x_train, y_train)
    y_pred, accuracy = evaluate(model, x_test, y_test)
    print("accuracy：", accuracy)

    model_path = get_env_variables()
    save_model(model, model_path)


if __name__ == '__main__':
    # my_pipeline()
    # my_pipeline.run_in_executor()

    mem_limit_train = "128m"
    mem_limit_service = "500m"
    cpu_count_train = 1
    cpu_count_service = 2

    current_directory = os.getcwd()
    volumes = {
        current_directory: {'bind': "/pipeline", 'mode': 'rw'}
    }
    hostname = "demo-host"
    ports = {'50051/tcp': 50051}
    service_dir = "demo"

    my_pipeline.deploy(
        volumes=volumes,
        skip_build_image=True,
        cpu_count=cpu_count_train,
        mem_limit=mem_limit_train
    ).to_service(
        hostname=hostname,
        ports=ports,
        volumes=volumes,
        tag=service_dir,
        cpu_count=cpu_count_service,
        mem_limit=mem_limit_service
    )
