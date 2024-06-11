FROM python:3.8
WORKDIR /microservices
COPY . .

# # Install python packages
# RUN pip install --upgrade pip
# RUN pip install -r requirements.txt
# RUN pip install mlgrpc-0.0.1-py3-none-any.whl

# Used by Chinese members
RUN pip install --upgrade pip -i https://mirrors.aliyun.com/pypi/simple
RUN pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple
RUN pip install aigear-0.0.1-py3-none-any.whl

# Port for GRPC
EXPOSE 50051

ENV PORT=50051 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONBUFFERED=1