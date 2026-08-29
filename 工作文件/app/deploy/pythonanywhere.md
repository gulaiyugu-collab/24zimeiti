# PythonAnywhere 试运行

PythonAnywhere 免费 Beginner 账户可以提供 `https://你的用户名.pythonanywhere.com`，但它不是 Docker 环境。此入口只用于受密码保护的网页预览；免费账户的外网白名单、磁盘和后台任务限制可能导致抖音采集、DeepSeek 和长时间转写不可用。

## 注册后操作

1. 在 PythonAnywhere 创建 Beginner（免费）账户并完成邮箱验证。不要升级付费方案。
2. 打开 Bash console，执行：

```bash
git clone https://github.com/gulaiyugu-collab/24zimeiti.git
cd 24zimeiti
python3 -m venv ~/.virtualenvs/project024
source ~/.virtualenvs/project024/bin/activate
pip install -r requirements-pythonanywhere.txt
```

3. 打开 Web 页面，新增 **Manual configuration**，Python 版本选择与控制台相同的版本。
4. Virtualenv 填：`/home/你的用户名/.virtualenvs/project024`。
5. WSGI configuration 文件中，把内容替换为：

```python
import os, sys
from pathlib import Path

root = Path('/home/你的用户名/24zimeiti')
sys.path.insert(0, str(root))
os.environ.setdefault('PROJECT024_ACCESS_USERNAME', 'project024')
os.environ['PROJECT024_ACCESS_PASSWORD'] = '这里填写你自己的长密码'
from pythonanywhere_wsgi import application
```

不要把真实密码提交到 GitHub。填入后点击 Web 页面上的 **Reload**，再打开 PythonAnywhere 给出的网址。浏览器弹出用户名密码时，用户名填 `project024`，密码填你自己设置的密码。

`/api/health` 可作为健康检查；它不需要密码。PythonAnywhere 免费环境不安装 `faster-whisper` 和 CUDA，因此上传媒体转写、本地 GPU 处理和长时间后台采集不属于本次免费预览的验收范围。
