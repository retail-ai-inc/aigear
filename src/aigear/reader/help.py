def get_help():
    print(
        "Adequate memory to use `pandas` package.\n"
        "Insufficient memory to use `Dask` package.\n"
        "<= 1TB: `DuckDB` and `Friends`.\n"
        "1 - 10TB: `Spark`, `Dask`, `Ray`, etc.\n"
        "> 10TB: `GPU-accelerated` Processing"
    )
