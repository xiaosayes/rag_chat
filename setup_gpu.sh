#!/bin/bash
# ============================================
# 文物知识库 RAG 系统 - GPU 服务器部署脚本
# 适用系统: Ubuntu 20.04+ / CentOS 7+
# 显卡: RTX 3090 (需安装 NVIDIA 驱动)
# ============================================
set -e

echo "========================================="
echo "  文物知识库 RAG 系统 - GPU 服务器部署"
echo "========================================="

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# 检查系统要求
check_requirements() {
    log_info "检查系统要求..."

    # 检查 NVIDIA 驱动
    if command -v nvidia-smi &> /dev/null; then
        NVIDIA_VERSION=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null || echo "unknown")
        GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null || echo "unknown")
        log_success "NVIDIA 驱动已安装: v${NVIDIA_VERSION}"
        log_success "GPU: ${GPU_NAME}"
    else
        log_warn "未检测到 NVIDIA 驱动！请先安装:"
        log_warn "  Ubuntu: sudo apt-get install nvidia-driver-525"
        log_warn "  CentOS: sudo yum install nvidia-driver"
        log_warn "或参考: https://developer.nvidia.com/cuda-downloads"
    fi

    # 检查 Python
    if command -v python3 &> /dev/null; then
        PY_VERSION=$(python3 --version 2>&1)
        log_success "Python: ${PY_VERSION}"
    else
        log_error "请先安装 Python 3.10+: sudo apt-get install python3.10 python3.10-dev"
        exit 1
    fi

    # 检查 Conda
    if command -v conda &> /dev/null; then
        CONDA_VERSION=$(conda --version 2>&1)
        log_success "Conda: ${CONDA_VERSION}"
    else
        log_warn "未检测到 Conda，尝试安装 Miniconda..."
        wget -q https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O /tmp/miniconda.sh
        bash /tmp/miniconda.sh -b -p $HOME/miniconda3
        export PATH="$HOME/miniconda3/bin:$PATH"
        echo 'export PATH="$HOME/miniconda3/bin:$PATH"' >> ~/.bashrc
        log_success "Miniconda 已安装"
    fi
}

# 创建 Conda 环境
create_conda_env() {
    local ENV_NAME="cultural-relics-rag"

    log_info "检查 Conda 环境: ${ENV_NAME}..."

    if conda env list | grep -q "${ENV_NAME}"; then
        log_warn "环境 ${ENV_NAME} 已存在，是否重新创建？ [y/N]"
        read -r REBUILD
        if [[ "$REBUILD" =~ ^[Yy]$ ]]; then
            log_info "删除旧环境..."
            conda env remove -n "${ENV_NAME}" -y
            log_info "创建新环境..."
            conda env create -f environment.yml
        else
            log_info "使用现有环境"
        fi
    else
        log_info "创建 Conda 环境: ${ENV_NAME}..."
        conda env create -f environment.yml
    fi

    log_success "Conda 环境就绪: ${ENV_NAME}"
}

# 安装项目依赖
install_deps() {
    local ENV_NAME="cultural-relics-rag"

    log_info "激活环境并安装项目依赖..."

    # 激活环境
    source "$(conda info --base)/etc/profile.d/conda.sh"
    conda activate "${ENV_NAME}"

    # 验证关键依赖
    log_info "验证关键依赖..."

    python3 -c "import dashscope; print('✓ dashscope:', dashscope.__version__)" 2>/dev/null || log_warn "dashscope 未安装"
    python3 -c "import qdrant_client; print('✓ qdrant-client')" 2>/dev/null || log_warn "qdrant-client 未安装"
    python3 -c "import torch; print('✓ PyTorch:', torch.__version__); print('  CUDA可用:', torch.cuda.is_available())" 2>/dev/null || log_warn "PyTorch 未安装或 CUDA 不可用"

    # 验证文档解析依赖
    python3 -c "from pypdf import PdfReader; print('✓ pypdf')" 2>/dev/null || log_warn "pypdf 未安装"
    python3 -c "from docx import Document; print('✓ python-docx')" 2>/dev/null || log_warn "python-docx 未安装"

    # 验证 PaddleOCR (GPU)
    log_info "检查 PaddleOCR..."
    python3 -c "
import paddle
print('✓ PaddlePaddle:', paddle.__version__)
print('  GPU可用:', paddle.is_compiled_with_cuda())
" 2>/dev/null || log_warn "PaddlePaddle 未安装或 GPU 不可用，OCR 功能将受限"

    log_success "依赖验证完成"
}

# 配置环境变量
setup_env() {
    log_info "配置环境变量..."

    if [ ! -f .env ]; then
        if [ -f .env.example ]; then
            cp .env.example .env
            log_info "已创建 .env 文件，请编辑并填入你的 DASHSCOPE_API_KEY"
            echo ""
            echo "请执行: nano .env"
            echo "将 DASHSCOPE_API_KEY=your_api_key_here 替换为你的实际 Key"
            echo ""
        else
            log_warn ".env.example 不存在，请手动创建 .env 文件"
        fi
    else
        log_info ".env 文件已存在"
    fi
}

# 构建知识库
build_knowledge_base() {
    log_info "准备构建知识库..."

    # 检查 API Key
    source .env 2>/dev/null || true
    if [ -z "${DASHSCOPE_API_KEY}" ]; then
        log_warn "DASHSCOPE_API_KEY 未设置，跳过知识库构建"
        log_info "请设置后手动运行:"
        log_info "  conda activate cultural-relics-rag"
        log_info "  python scripts/build_knowledge_base.py"
        return
    fi

    # 生成测试数据
    log_info "生成 Mock 文物数据..."
    python scripts/generate_mock_data.py -n 50

    # 生成测试文档
    log_info "生成多格式测试文档..."
    python scripts/generate_test_docs.py

    # 构建知识库
    log_info "构建知识库..."
    python scripts/build_knowledge_base.py --source mixed

    log_success "知识库构建完成！"
}

# 运行测试
run_tests() {
    log_info "运行单元测试..."
    source "$(conda info --base)/etc/profile.d/conda.sh"
    conda activate cultural-relics-rag
    python -m pytest tests/ -v --tb=short || log_warn "部分测试失败，请检查"
    log_success "测试完成"
}

# 主流程
main() {
    echo ""
    echo "========================================="
    echo "  开始部署..."
    echo "========================================="
    echo ""

    check_requirements
    echo ""

    create_conda_env
    echo ""

    install_deps
    echo ""

    setup_env
    echo ""

    build_knowledge_base
    echo ""

    run_tests
    echo ""

    echo "========================================="
    echo -e "${GREEN}  部署完成！${NC}"
    echo "========================================="
    echo ""
    echo "快速使用:"
    echo ""
    echo "  # 激活环境"
    echo "  conda activate cultural-relics-rag"
    echo ""
    echo "  # 交互式问答"
    echo "  python scripts/run_qa.py"
    echo ""
    echo "  # 单次查询"
    echo "  python scripts/run_qa.py -q '推荐一些代表性的文物'"
    echo ""
    echo "  # 查看知识库统计"
    echo "  python scripts/run_qa.py --stats"
    echo ""
    echo "========================================="
}

main