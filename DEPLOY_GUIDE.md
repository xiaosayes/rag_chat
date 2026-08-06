# GPU 服务器部署指南

> 本文档指导你将项目从 Windows 开发环境打包部署到 Linux GPU 服务器。

---

## 目录

- [前提条件](#前提条件)
- [步骤 1: 项目打包](#步骤-1-项目打包)
- [步骤 2: 上传到服务器](#步骤-2-上传到服务器)
- [步骤 3: 服务器环境准备](#步骤-3-服务器环境准备)
- [步骤 4: 配置 API Key](#步骤-4-配置-api-key)
- [步骤 5: 生成数据并构建知识库](#步骤-5-生成数据并构建知识库)
- [步骤 6: 启动问答服务](#步骤-6-启动问答服务)
- [步骤 7: 验证服务](#步骤-7-验证服务)
- [可选: 独立部署多个项目](#可选-独立部署多个项目)
- [可选: 使用 systemd 管理服务](#可选-使用-systemd-管理服务)
- [可选: 使用 Nginx 反向代理](#可选-使用-nginx-反向代理)
- [常见问题排查](#常见问题排查)

---

## 前提条件

- **目标服务器**：Linux（Ubuntu 20.04+ / CentOS 7+），已安装 NVIDIA 驱动（如需 PaddleOCR GPU）
- **网络**：服务器可访问外网（需调用阿里云百炼 API）
- **Python**：≥ 3.10
- **API Key**：阿里云百炼 API Key

---

## 步骤 1: 项目打包

在 Windows 开发机上，将项目打包为 tar.gz：

```bash
# 在项目根目录执行
cd E:/project/agent_project/pi/test

# 打包到上级目录，避免 "Can't add archive to itself" 错误
# 注意：--exclude=.env 排除本地 .env（含 API Key），服务器上需重新创建
tar -czf ../project.tar.gz ^
  --exclude=__pycache__ ^
  --exclude=.pytest_cache ^
  --exclude=.git ^
  --exclude=venv ^
  --exclude=data/processed ^
  --exclude=logs ^
  --exclude=*.pyc ^
  --exclude=.env ^
  .

:: 打包产物在上一级目录：E:/project/agent_project/project.tar.gz
:: 如果本地没有 tar 命令，可以用 git bash 或 WSL 执行
```

或者使用 PowerShell（没有 `-Exclude` 参数时，手动删除不需要的文件后打包）：

```powershell
# PowerShell 中执行
cd E:/project/agent_project/pi/test

# 先删除不需要的目录
Remove-Item -Recurse -Force __pycache__/, .pytest_cache/, venv/, logs/, data/processed/ -ErrorAction SilentlyContinue

# 再打包
Compress-Archive -Path .\* -DestinationPath ..\project.zip
```

> **注意**：打包时不包含 `data/processed/`（知识库数据），因为需要在服务器上重新构建。
> **替代方案**：Windows 10+ 自带 `tar` 命令，无需安装：
> ```cmd
> tar -czf project.tar.gz --exclude=__pycache__ --exclude=.pytest_cache --exclude=venv --exclude=logs --exclude=data/processed .
> ```

---

## 步骤 2: 上传到服务器

使用 SCP 或 SFTP 将打包文件上传到服务器：

```bash
# 本地执行（Windows PowerShell / WSL）
scp project.tar.gz user@your-server-ip:/home/user/project/

# 或使用 rsync（需要 WSL 或 Git Bash）
rsync -avz --exclude='__pycache__' --exclude='.pytest_cache' --exclude='venv' --exclude='data/processed' --exclude='logs' --exclude='*.pyc' ./ user@your-server-ip:/home/user/project/
```

---

## 步骤 3: 服务器环境准备

### 3.1 连接服务器

```bash
ssh user@your-server-ip
```

### 3.2 解压项目

```bash
cd /home/user/project
tar -xzf project.tar.gz
```

### 3.3 创建 Conda 环境（推荐）

```bash
# 从 environment.yml 创建环境
conda env create -f environment.yml

# 激活环境
conda activate cultural-relics-rag

# 如果 Conda 环境已存在，直接激活
# conda activate cultural-relics-rag
```

### 3.4 安装依赖

```bash
# 升级 pip
pip install --upgrade pip

# 安装核心依赖
pip install -r requirements.txt
```

### 3.5 安装 PaddleOCR（可选，如需 OCR 图片识别）

```bash
# 安装 PaddlePaddle GPU 版
pip install paddlepaddle-gpu>=2.6.0

# 验证 GPU 可用
python -c "import paddle; print('GPU可用:', paddle.is_compiled_with_cuda())"

# 安装 PaddleOCR
pip install paddleocr>=2.7.0
```

### 3.6 安装 scikit-learn（可选，用于重排序降级）

```bash
pip install scikit-learn>=1.3.0
```

---

## 步骤 4: 配置 API Key

```bash
# 方式一：环境变量（推荐）
export DASHSCOPE_API_KEY="your-api-key-here"

# 持久化到 ~/.bashrc
echo 'export DASHSCOPE_API_KEY="your-api-key-here"' >> ~/.bashrc
source ~/.bashrc

# 方式二：创建 .env 文件
cp .env.example .env
nano .env  # 编辑 .env 文件，填入 API Key
```

---

## 步骤 5: 生成数据并构建知识库

### 5.1 生成 Mock 测试数据

```bash
# 确保在 Conda 环境中
conda activate cultural-relics-rag

# 生成两个项目的测试数据
python scripts/generate_mock_project_data.py
```

### 5.2 构建博物馆项目知识库

```bash
# 构建知识库（首次构建需要调用 Embedding API，约 30-60 秒）
python scripts/build_knowledge_base.py --project museum --source json
```

成功输出示例：
```
============================================================
知识库构建完成！
  - artifacts: 17
  - chunks: 42
  - vectors: 42
============================================================
```

### 5.3 构建企业项目知识库

```bash
python scripts/build_knowledge_base.py --project enterprise --source json
```

### 5.4 验证知识库

```bash
# 查看所有可用项目
python scripts/build_knowledge_base.py --list-projects

# 测试查询（非交互式）
python scripts/run_qa.py -q "推荐一些代表性的文物" --project museum
```

---

## 步骤 6: 启动问答服务

### 6.1 启动单个项目（前台）

```bash
# 启动博物馆项目 Web UI
python app.py --project museum --host 0.0.0.0 --port 7860
```

访问 `http://your-server-ip:7860` 即可使用。

### 6.2 后台运行（使用 nohup）

```bash
# 后台运行，日志输出到文件
nohup python app.py --project museum --host 0.0.0.0 --port 7860 > app_museum.log 2>&1 &

# 查看日志
tail -f app_museum.log
```

### 6.3 后台运行（使用 tmux 推荐）

```bash
# 安装 tmux
sudo apt-get install -y tmux

# 创建新会话
tmux new -s museum

# 在 tmux 会话中启动
python app.py --project museum --host 0.0.0.0 --port 7860

# 按 Ctrl+B 然后按 D 分离会话（不中断服务）

# 重新连接会话
tmux attach -t museum

# 列出所有会话
tmux ls
```

---

## 步骤 7: 验证服务

### 7.1 检查服务是否运行

```bash
# 查看进程
ps aux | grep app.py

# 查看端口
ss -tlnp | grep 7860
```

### 7.2 测试 API 响应

```bash
# 使用 curl 测试
curl http://localhost:7860

# 应返回 Gradio 页面 HTML
```

### 7.3 查看日志

```bash
# 查看运行日志
tail -f app_museum.log

# 查看系统日志
cat logs/rag_*.log
```

---

## 可选: 独立部署多个项目

```bash
# 终端1（tmux 会话1）：博物馆项目
tmux new -s museum
python app.py --project museum --host 0.0.0.0 --port 7860

# 终端2（tmux 会话2）：企业项目
tmux new -s enterprise
python app.py --project enterprise --host 0.0.0.0 --port 7861

# 终端3（tmux 会话3）：自定义项目
tmux new -s custom
python app.py --project custom --host 0.0.0.0 --port 7862
```

---

## 可选: 使用 systemd 管理服务

创建 systemd 服务文件，实现开机自启和自动重启。

### 创建服务文件

```bash
sudo nano /etc/systemd/system/rag-museum.service
```

写入以下内容：

```ini
[Unit]
Description=知识库 RAG 问答系统 - 博物馆项目
After=network.target

[Service]
Type=simple
User=user
WorkingDirectory=/home/user/project
Environment="DASHSCOPE_API_KEY=your-api-key-here"
# 使用 conda run 在 Conda 环境中执行
ExecStart=/home/user/miniconda3/bin/conda run -n cultural-relics-rag python app.py --project museum --host 0.0.0.0 --port 7860
Restart=always
RestartSec=10
StandardOutput=append:/home/user/project/app_museum.log
StandardError=append:/home/user/project/app_museum.log

[Install]
WantedBy=multi-user.target
```

> **注意**：请将 `/home/user/miniconda3` 替换为你的实际 Conda 安装路径。可通过 `which conda` 查看。

### 启动服务

```bash
# 重新加载 systemd
sudo systemctl daemon-reload

# 启动服务
sudo systemctl start rag-museum

# 设置开机自启
sudo systemctl enable rag-museum

# 查看状态
sudo systemctl status rag-museum

# 查看日志
sudo journalctl -u rag-museum -f
```

---

## 可选: 使用 Nginx 反向代理

### 安装 Nginx

```bash
sudo apt-get install -y nginx
```

### 配置反向代理

```bash
sudo nano /etc/nginx/sites-available/rag
```

```nginx
server {
    listen 80;
    server_name your-domain.com;

    # 博物馆项目
    location /museum/ {
        proxy_pass http://127.0.0.1:7860/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }

    # 企业项目
    location /enterprise/ {
        proxy_pass http://127.0.0.1:7861/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
```

### 启用配置

```bash
sudo ln -s /etc/nginx/sites-available/rag /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

---

## 常见问题排查

### Q: 启动时提示"知识库未构建"

```bash
# 运行构建脚本
conda activate cultural-relics-rag
python scripts/build_knowledge_base.py --project museum --source json
```

### Q: Embedding API 调用失败

```bash
# 检查 API Key 是否配置正确
echo $DASHSCOPE_API_KEY
# 如果为空，重新设置环境变量

# 测试 API 连通性
python -c "
from dashscope import TextEmbedding
resp = TextEmbedding.call(model='text-embedding-v3', input='测试', api_key='$DASHSCOPE_API_KEY')
print('状态码:', resp.status_code)
"
```

### Q: 端口被占用

```bash
# 查看端口占用
sudo lsof -i :7860

# 或使用 ss
ss -tlnp | grep 7860

# 使用其他端口
python app.py --project museum --port 7862
```

### Q: 内存不足

```bash
# 查看内存使用
free -h

# 如果内存不足，可以：
# 1. 减少向量维度（修改 .env 中的 EMBEDDING_DIMENSION=512）
# 2. 减少缓存容量（修改 src/cache.py 中的容量参数）
```

### Q: GPU 显存不足（PaddleOCR）

```bash
# 使用 CPU 模式运行 OCR
python scripts/build_knowledge_base.py --project museum --source json --no-ocr

# 或者在 document_loader.py 中设置 use_gpu=False
```

---

## 一键部署脚本

如果以上步骤太繁琐，可以使用项目自带的 `setup_gpu.sh` 脚本：

```bash
# 上传项目到服务器后
cd /home/user/project

# 给脚本执行权限
chmod +x setup_gpu.sh

# 运行一键部署脚本
bash setup_gpu.sh
```

> 注意：脚本会自动安装依赖、生成数据、构建知识库。但需要提前配置好 `DASHSCOPE_API_KEY` 环境变量。

---

## 部署检查清单

- [ ] 项目文件已上传到服务器
- [ ] Python 虚拟环境已创建并激活
- [ ] 所有依赖已安装（`pip install -r requirements.txt`）
- [ ] `DASHSCOPE_API_KEY` 已配置
- [ ] Mock 数据已生成（`python scripts/generate_mock_project_data.py`）
- [ ] 知识库已构建（`python scripts/build_knowledge_base.py --project museum --source json`）
- [ ] Web UI 已启动（`python app.py --project museum --host 0.0.0.0 --port 7860`）
- [ ] 服务可正常访问（`curl http://localhost:7860`）
- [ ] 测试问答正常（`python scripts/run_qa.py -q "推荐一些代表性的文物" --project museum`）