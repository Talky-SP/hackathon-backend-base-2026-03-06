FROM public.ecr.aws/lambda/python:3.11

# System deps for openpyxl / pandas / etc.
RUN yum install -y gcc gcc-c++ && yum clean all

# Upgrade pip so pre-built wheels (tiktoken, numpy, etc.) are found
RUN pip install --upgrade pip

# Copy requirements first (cache layer)
COPY requirements-lambda.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir --only-binary tiktoken -r ${LAMBDA_TASK_ROOT}/requirements.txt

# Copy the full agent package
COPY hackathon_backend/ ${LAMBDA_TASK_ROOT}/hackathon_backend/

# Lambda handler entry point
CMD ["hackathon_backend.services.lambdas.agent.lambda_handler.handler"]
