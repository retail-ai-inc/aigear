from string import Template

with open('Dockerfile_template', 'r') as f:
    dockerfile_template = f.read()


template_params = {
    'python_version': '3.9',
    'install_command': 'pip install --no-cache-dir -r requirements.txt'
}

dockerfile_content = Template(dockerfile_template).safe_substitute(template_params)


with open('Dockerfile', 'w') as f:
    f.write(dockerfile_content)

print("Dockerfile 创建完成。")
