FROM python:3.11-slim

WORKDIR /app

# 先装依赖 (利用 Docker 层缓存)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷代码
COPY . .

# 数据目录 (命名卷挂载点)
RUN mkdir -p /app/data

EXPOSE 8787

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8787"]
