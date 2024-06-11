FROM python:3.9
WORKDIR /aigear/aigear/
COPY . /aigear/aigear/
COPY requirements.txt /aigear/aigear/requirements.txt
RUN python -m pip install --upgrade pip
RUN python -m pip install -r /aigear/aigear/requirements.txt
