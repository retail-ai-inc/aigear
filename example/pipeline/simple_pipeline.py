from aigear.pipeline.types import Parallel, NBParallel
from aigear.pipeline.task import async_task, async_nb_task
from aigear.pipeline.pipeline import Pipeline
import time
import asyncio

@async_task
def sequential_step1(a, b):
    print(f"sequential step 1: a({a}), b({b})")
    return a, b

@async_task
def sequential_step2(c, a, b):
    print(f"sequential step 2: d({a + b + c})=c({c}) + a({a}) + b({b})")
    return a + b + c

@async_task
def sequential_step3(a, b):
    print(f"sequential step 3: d({a + b})=a({a}) + b({b})")
    return a + b

@async_task
def parallel_step1(a):
    if block:
        time.sleep(2)
    print(f"parallel step 1: a({a})")
    return a

@async_task
def parallel_step2(b):
    if block:
        time.sleep(1)
    print(f"parallel step 2: b({b})")
    return b

@async_nb_task
def parallel_step3(a):
    if block:
        time.sleep(2)
    print(f"nb parallel step 3: a({a})")
    return a

@async_nb_task
def parallel_step4(b):
    if block:
        time.sleep(1)
    print(f"nb parallel step 4: b({b})")
    return b


def my_pipeline1():
    pipeline = Pipeline(
        name="template",
        version="0.0.1",
        describe="",
    )

    step1 = pipeline.step(sequential_step1, (1, 1), "step1_output")
    step2 = pipeline.step(sequential_step2, (1, {"rely": "step1_output"}), "step2_output")
    print(step1)

    my_workflow = pipeline.workflow(
        step1,
        step2,
    )
    print(my_workflow)

    print("------start------")
    start_time = time.time()

    result = pipeline.run()
    print("Pipeline result:", result.get("step2_output"))

    end_time = time.time()
    print('Run time: ', end_time - start_time)
    print("------end------")


def my_pipeline2():
    pipeline = Pipeline(
        name="template",
        version="0.0.1",
        describe="",
    )

    step3 = pipeline.step(parallel_step1, 1, "step3_output")
    step4 = pipeline.step(parallel_step2, 2, "step4_output")
    step5 = pipeline.step(sequential_step3, {"rely": ["step3_output", "step4_output"]}, "step5_output")

    my_workflow = pipeline.workflow(
        # Parallel(step3, step4),
        step3, step4,
        step5,
    )
    print(my_workflow)

    print("------start------")
    start_time = time.time()

    result = pipeline.run()
    print("Pipeline result:", result.get("step5_output"))

    end_time = time.time()
    print('Run time: ', end_time - start_time)
    print("------end------")


def my_pipeline3():
    pipeline = Pipeline(
        name="template",
        version="0.0.1",
        describe="",
    )

    step7 = pipeline.step(parallel_step3, 1, "step6_output")
    step8 = pipeline.step(parallel_step4, 2, "step7_output")
    step9 = pipeline.step(sequential_step3, {"rely": ["step6_output", "step7_output"]}, "step9_output")

    my_workflow = pipeline.workflow(
        # NBParallel(step7, step8),
        step7, step8,
        step9,
    )
    print(my_workflow)

    print("------start------")
    start_time = time.time()

    result = pipeline.run()
    print("Pipeline result:", result.get("step9_output"))

    end_time = time.time()
    print('Run time: ', end_time - start_time)
    print("------end------")


if __name__ == "__main__":
    # Simulate IO blocking
    # For IO intensive types, non blocking will be 1s faster than blocking.
    # On the contrary, for general processing, non blocking will be slower
    block = False

    my_pipeline1()
    print('--------------------------------------------')
    my_pipeline2()
    print('--------------------------------------------')
    my_pipeline3()
