FROM python:3.9
WORKDIR /pipeline
COPY . .

# Used by Chinese members
RUN pip install --upgrade pip -i https://mirrors.aliyun.com/pypi/simple
RUN pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple
RUN pip install aigear-0.0.1-py3-none-any.whl

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONBUFFERED=1
