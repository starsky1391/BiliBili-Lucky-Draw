FROM python:3.10-slim

# 复制项目文件到容器中
COPY . /app

# 设置工作目录为项目目录
WORKDIR /app

# 安装项目依赖项
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.org/simple

# 运行docker run命令
CMD ["python", "main.py"]
