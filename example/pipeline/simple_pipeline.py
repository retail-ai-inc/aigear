from aigear.pipeline import Pipeline, Parallel, NBParallel, InputParams, RelyParams
import time


def sequential_step1(a, b):
    print(f"sequential step 1: a({a}), b({b})")
    return a, b


def sequential_step2(c, a, b):
    print(f"sequential step 2: d({a + b + c})=c({c}) + a({a}) + b({b})")
    return a + b + c


def sequential_step3(a, b):
    print(f"sequential step 3: d({a + b})=a({a}) + b({b})")
    return a + b


def parallel_step1(a):
    time.sleep(2)
    print(f"parallel step 1: a({a})")
    return a


def parallel_step2(b):
    time.sleep(1)
    print(f"parallel step 2: b({b})")
    return b


def parallel_step3(a):
    time.sleep(2)
    print(f"nb parallel step 3: a({a})")
    return a


def parallel_step4(b):
    time.sleep(1)
    print(f"nb parallel step 4: b({b})")
    return b


def my_pipeline():
    pipeline = Pipeline(
        name="template",
        version="0.0.1",
        describe="",
    )

    # Dependency relationship
    step1 = pipeline.step(sequential_step1, InputParams(1, 1), "step1_output")
    step2 = pipeline.step(sequential_step2, InputParams(1, RelyParams("step1_output")), "step2_output")
    step3 = pipeline.step(parallel_step1, InputParams(1), "step3_output")
    step4 = pipeline.step(parallel_step2, InputParams(2), "step4_output")
    step5 = pipeline.step(
        sequential_step3,
        InputParams(
            RelyParams("step3_output"),
            RelyParams("step4_output")
        ),
        "step5_output"
    )
    step6 = pipeline.step(parallel_step3, InputParams(1), "step6_output")
    step7 = pipeline.step(parallel_step4, InputParams(2), "step7_output")
    step8 = pipeline.step(
        sequential_step3,
        InputParams(
            RelyParams("step6_output"),
            RelyParams("step7_output")
        ),
        "step8_output"
    )
    print(step1)

    # Operation mode
    my_workflow = pipeline.workflow(
        step1,
        step2,
        Parallel(step3, step4),
        step5,
        NBParallel(step6, step7),
        step8,
    )
    print(my_workflow)

    print("------start------")
    start_time = time.time()

    result = pipeline.run()
    print("Pipeline result:", result.get("step2_output"))
    print("Pipeline result:", result.get("step5_output"))
    print("Pipeline result:", result.get("step8_output"))

    end_time = time.time()
    print('Run time: ', end_time - start_time)
    print("------end------")


if __name__ == "__main__":
    my_pipeline()
