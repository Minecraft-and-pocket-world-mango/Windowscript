import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
import sys
import io
import re
import os
import datetime

sys.setrecursionlimit(10000)

VERSION = "2.1.1-20260902-18"

AUTO_IMPORTABLE = {
    'random', 'math', 'time', 'datetime', 'os', 'sys', 'json', 're',
    'string', 'itertools', 'functools', 'collections', 'statistics',
    'decimal', 'fractions', 'pathlib', 'subprocess', 'threading',
    'queue', 'uuid', 'base64', 'hashlib', 'socket', 'shutil', 'glob',
    'tempfile', 'copy', 'traceback', 'textwrap', 'array', 'enum',
    'secrets', 'bisect', 'heapq', 'calendar', 'csv',
}

LARGE_RETURN_THRESHOLD = 100


def transpile(code):
    lines = code.split('\n')
    output = []
    indent = 0
    i = 0
    total_lines = len(lines)
    INDENT = '    '

    pending_imports = []
    auto_imported = set()
    skip_politeness = False
    python_code_allowed = False

    def get_block(start_idx):
        block_lines = []
        brace_count = 0
        j = start_idx
        while j < total_lines:
            line = lines[j]
            if '//' in line:
                code_part = line.split('//')[0]
            else:
                code_part = line
            brace_count += code_part.count('{') - code_part.count('}')
            block_lines.append(line)
            if brace_count == 0 and j > start_idx:
                break
            j += 1
        return block_lines, j

    def strip_comments(line):
        """移除行内注释，返回纯代码部分"""
        if '//' in line:
            return line.split('//')[0].rstrip()
        return line

    def find_matching_brace(lines_list, start_idx):
        """从 start_idx 行开始，找到匹配的闭合大括号所在行索引。
        假定 start_idx 行包含了开括号 { """
        brace_count = 0
        j = start_idx
        while j < len(lines_list):
            code_part = strip_comments(lines_list[j])
            brace_count += code_part.count('{') - code_part.count('}')
            if brace_count <= 0:
                return j
            j += 1
        return len(lines_list) - 1

    def parse_class_body_to_python(class_lines, base_indent_str):
        """将 class 块内容转译为 Python class 定义代码行列表。
        class_lines[0] 是 'class 类名 {' 行，
        class_lines[-1] 是 '}' 行。"""
        header = class_lines[0].strip()
        m = re.match(r'class\s+(\w+)\s*\{', header)
        if not m:
            return [f'{base_indent_str}# 错误: class 声明格式错误 -> {header}']
        class_name = m.group(1)

        # 提取 class 块内部内容（去掉首行和末行）
        body_lines = class_lines[1:-1]

        out = []
        out.append(f'{base_indent_str}class {class_name}:')

        # 收集 private 块
        private_attrs = []       # [(name, value_expr)]
        private_methods = []     # [(name, params, body_lines)]
        new_params = None        # new() 的参数
        new_body_lines = []      # new() 的方法体行
        public_methods = []      # [(name, params, body_lines)]

        idx = 0
        while idx < len(body_lines):
            raw = body_lines[idx]
            stripped = raw.strip()

            # 跳过空行和注释
            if not stripped or stripped.startswith('//') or stripped.startswith('#'):
                idx += 1
                continue

            # private 块
            if stripped == 'private {' or re.match(r'private\s*\{', stripped):
                # 找到 private 块的闭合括号
                priv_end = find_matching_brace(body_lines, idx)
                priv_body = body_lines[idx + 1:priv_end]
                idx = priv_end + 1

                # 解析 private 块内容
                pi = 0
                while pi < len(priv_body):
                    pline = priv_body[pi].strip()
                    if not pline or pline.startswith('//') or pline.startswith('#'):
                        pi += 1
                        continue

                    # 变量声明 v name = value
                    vm = re.match(r'v\s+(\w+)\s*=\s*(.+)', pline)
                    if vm:
                        private_attrs.append((vm.group(1), vm.group(2)))
                        pi += 1
                        continue

                    # 方法定义 d name(params) {
                    dm = re.match(r'd\s+(\w+)\s*\(([^)]*)\)\s*\{', pline)
                    if dm:
                        mname = dm.group(1)
                        mparams = dm.group(2)
                        # 找方法体
                        method_end = find_matching_brace(priv_body, pi)
                        method_body = priv_body[pi + 1:method_end]
                        private_methods.append((mname, mparams, method_body))
                        pi = method_end + 1
                        continue

                    # 其他语句（允许在 private 中直接写一些初始化代码）
                    pi += 1
                continue

            # new(params) { 构造方法
            nm = re.match(r'new\s*\(([^)]*)\)\s*\{', stripped)
            if nm:
                new_params = nm.group(1)
                new_end = find_matching_brace(body_lines, idx)
                new_body_lines = body_lines[idx + 1:new_end]
                idx = new_end + 1
                continue

            # d 公开方法(params) {
            dm = re.match(r'd\s+(\w+)\s*\(([^)]*)\)\s*\{', stripped)
            if dm:
                mname = dm.group(1)
                mparams = dm.group(2)
                method_end = find_matching_brace(body_lines, idx)
                method_body = body_lines[idx + 1:method_end]
                public_methods.append((mname, mparams, method_body))
                idx = method_end + 1
                continue

            idx += 1

        class_indent = base_indent_str + INDENT

        # 收集所有私有属性名和私有方法名
        attr_names = set(name for name, _ in private_attrs)
        private_method_names = set(name for name, _, _ in private_methods)

        # 生成 __init__ 方法
        # 将 private 属性初始化 + new 方法体合并到 __init__
        has_init = (private_attrs or new_body_lines is not None)
        if has_init:
            if new_params is not None:
                params_str = 'self'
                if new_params.strip():
                    params_str = f'self, {new_params}'
                out.append(f'{class_indent}def __init__({params_str}):')
            else:
                out.append(f'{class_indent}def __init__(self):')

            init_indent = class_indent + INDENT

            if private_attrs:
                for attr_name, attr_val in private_attrs:
                    out.append(f'{init_indent}self.{attr_name} = {attr_val}')

            if new_body_lines:
                # 收集 new 方法参数名，避免参数被误加 self.
                new_param_names = set()
                if new_params:
                    for p in new_params.split(','):
                        p = p.strip()
                        if p:
                            new_param_names.add(p)
                # new 方法体中不自动加 self. 前缀给参数
                body_out = transpile_method_body(
                    new_body_lines, init_indent, attr_names, private_method_names, new_param_names)
                out.extend(body_out)

            if not private_attrs and not new_body_lines:
                out.append(f'{init_indent}pass')

        # 生成私有方法（使用单下划线前缀，便于类内调用）
        for mname, mparams, mbody in private_methods:
            params_str = 'self'
            if mparams.strip():
                params_str = f'self, {mparams}'
            out.append(f'{class_indent}def _{mname}({params_str}):')
            method_indent = class_indent + INDENT
            # 收集方法参数名
            method_param_names = set()
            if mparams:
                for p in mparams.split(','):
                    p = p.strip()
                    if p:
                        method_param_names.add(p)
            body_out = transpile_method_body(
                mbody, method_indent, attr_names, private_method_names, method_param_names)
            if body_out:
                out.extend(body_out)
            else:
                out.append(f'{method_indent}pass')

        # 生成公开方法
        for mname, mparams, mbody in public_methods:
            params_str = 'self'
            if mparams.strip():
                params_str = f'self, {mparams}'
            out.append(f'{class_indent}def {mname}({params_str}):')
            method_indent = class_indent + INDENT
            # 收集方法参数名
            method_param_names = set()
            if mparams:
                for p in mparams.split(','):
                    p = p.strip()
                    if p:
                        method_param_names.add(p)
            body_out = transpile_method_body(
                mbody, method_indent, attr_names, private_method_names, method_param_names)
            if body_out:
                out.extend(body_out)
            else:
                out.append(f'{method_indent}pass')

        # 如果类没有任何内容
        if not has_init and not private_methods and not public_methods:
            out.append(f'{class_indent}pass')

        return out

    def parse_class_line(stripped, attr_names=None, private_method_names=None):
        """转译类内部方法体中的单行代码。
        将 this. 替换为 self.，处理 v/pr/d/if/es/from to/} 等语句。
        方法体中引用类属性时自动添加 self. 前缀。
        方法体中调用私有方法时自动添加 _ 前缀。"""
        # this. -> self.
        stripped = stripped.replace('this.', 'self.')

        # 跳过纯大括号行
        if stripped == '}' or stripped == '{':
            return None

        # 移除行尾的 { （控制流块开始）
        # if (condition) { -> if (condition):
        m = re.match(r'if\s+(.+)\s*\{$', stripped)
        if m:
            condition = m.group(1).strip()
            condition = add_self_prefix(condition, attr_names)
            condition = add_private_method_prefix(
                condition, private_method_names)
            return f'if {condition}:'

        # from to (condition) { -> while (condition):
        m = re.match(r'from\s+to\s*\((.+?)\)\s*\{$', stripped)
        if m:
            condition = m.group(1).strip()
            if condition in ('false', 'False'):
                return 'if False:'
            else:
                condition = add_self_prefix(condition, attr_names)
                condition = add_private_method_prefix(
                    condition, private_method_names)
                return f'while {condition}:'

        # es -> else:
        if stripped == 'es':
            return 'else:'

        # else if (condition) { -> elif (condition):
        m = re.match(r'es\s+if\s+(.+)\s*\{$', stripped)
        if m:
            condition = m.group(1).strip()
            condition = add_self_prefix(condition, attr_names)
            condition = add_private_method_prefix(
                condition, private_method_names)
            return f'elif {condition}:'

        # 注释
        if stripped.startswith('//') or stripped.startswith('#'):
            return stripped.replace('//', '#', 1)

        # 变量声明 v name = value -> 直接赋值（局部变量）
        m = re.match(r'v\s+(\w+)\s*=\s*(.+)', stripped)
        if m:
            return f'{m.group(1)} = {m.group(2)}'

        # pr 语句
        if stripped.startswith('pr '):
            content = stripped[2:].strip()
            content = content.replace('this.', 'self.')
            content = add_self_prefix(content, attr_names)
            content = add_private_method_prefix(content, private_method_names)
            return f'print({content})'

        m = re.match(r'pr\s*\((.+)\)', stripped)
        if m:
            content = m.group(1).replace('this.', 'self.')
            content = add_self_prefix(content, attr_names)
            content = add_private_method_prefix(content, private_method_names)
            return f'print({content})'

        # rz 返回语句
        if stripped.startswith('rz '):
            value = stripped[2:].strip()
            value = value.replace('this.', 'self.')
            value = add_self_prefix(value, attr_names)
            value = add_private_method_prefix(value, private_method_names)
            return f'return {value}'

        # 其他表达式语句（如赋值、方法调用等）
        # 自动为裸属性名添加 self. 前缀
        # 自动为私有方法调用添加 _ 前缀
        result = add_self_prefix(stripped, attr_names)
        result = add_private_method_prefix(result, private_method_names)
        return result

    def add_self_prefix(expr, attr_names=None):
        """在表达式中为裸引用的类属性名添加 self. 前缀。
        例如：health -= dmg -> self.health -= dmg
             name + "x" -> self.name + "x"
        但不替换已有 . 前缀的、作为参数的、局部变量等。"""
        if not attr_names:
            return expr
        for attr in attr_names:
            # 匹配 attr 名前面不是 . 或字母/数字/下划线的位置
            # 即 attr 作为独立标识符出现
            # 使用负向先行断言：前面不是 . 或 \w
            expr = re.sub(r'(?<![\w.])' + re.escape(attr) +
                          r'\b', f'self.{attr}', expr)
        return expr

    def add_private_method_prefix(expr, private_method_names=None):
        """在表达式中为私有方法调用添加 _ 前缀。
        例如：target.扣血(15) -> target._扣血(15)
             死亡() -> self._死亡()"""
        if not private_method_names:
            return expr
        for method_name in private_method_names:
            # 匹配 method_name 后面跟着 ( 的情况
            # 处理 target.扣血( 的情况
            expr = re.sub(r'(\.' + re.escape(method_name) +
                          r')(?=\()', f'._\\1', expr) if False else expr
            # 正确的处理方式
            expr = re.sub(r'(?<![\w.])' + re.escape(method_name) +
                          r'(?=\()', f'self._{method_name}', expr)
            # 处理 对象.方法名( 的情况
            expr = re.sub(r'\.' + re.escape(method_name) +
                          r'(?=\()', f'._{method_name}', expr)
        return expr

    def transpile_method_body(body_lines, base_indent_str, attr_names=None, private_method_names=None, param_names=None):
        """转译方法体，正确处理嵌套控制流块的缩进。
        body_lines 是方法体的原始行列表（不含方法定义行和闭合括号行）。
        base_indent_str 是方法体的基础缩进。
        attr_names 是类属性名集合，用于自动添加 self. 前缀。
        private_method_names 是私有方法名集合，用于添加 _ 前缀。
        param_names 是方法参数名集合，用于避免参数被误加 self. 前缀。"""
        if attr_names is None:
            attr_names = set()
        if private_method_names is None:
            private_method_names = set()
        if param_names is None:
            param_names = set()

        # 从属性名中排除参数名，避免参数被误加 self.
        effective_attrs = attr_names - param_names

        out = []
        # 使用栈来跟踪缩进层级
        current_indent = base_indent_str
        indent_stack = []   # 保存进入控制流块前的缩进

        for line in body_lines:
            stripped = line.strip()
            if not stripped or stripped.startswith('//') or stripped.startswith('#'):
                continue

            # 闭合括号 - 减少缩进
            if stripped == '}':
                if indent_stack:
                    current_indent = indent_stack.pop()
                continue

            # 开括号行（单独的 {）
            if stripped == '{':
                indent_stack.append(current_indent)
                current_indent = current_indent + INDENT
                continue

            parsed = parse_class_line(
                stripped, effective_attrs, private_method_names)
            if parsed is None:
                continue

            # 检查是否是控制流开始（以 : 结尾）
            is_block_start = parsed.rstrip().endswith(':')

            out.append(f'{current_indent}{parsed}')

            if is_block_start:
                indent_stack.append(current_indent)
                current_indent = current_indent + INDENT

        return out

    def transpile_use_class_block(block_lines, base_indent_str):
        """转译 use.class(对象名) { public { ... } } 块"""
        header = block_lines[0].strip()
        m = re.match(r'use\.class\s*\(\s*(\w+)\s*\)\s*\{', header)
        if not m:
            return [f'{base_indent_str}# 错误: use.class 声明格式错误 -> {header}']

        obj_name = m.group(1)

        # 提取 use.class 块内部内容
        body_lines = block_lines[1:-1]

        out = []

        # 查找 public 块
        idx = 0
        while idx < len(body_lines):
            stripped = body_lines[idx].strip()

            if not stripped or stripped.startswith('//') or stripped.startswith('#'):
                idx += 1
                continue

            if stripped == 'public' or re.match(r'public\s*\{?', stripped):
                # 找到 public 块的开始
                if '{' in stripped:
                    pub_end = find_matching_brace(body_lines, idx)
                    pub_body = body_lines[idx + 1:pub_end]
                    idx = pub_end + 1
                else:
                    idx += 1
                    if idx < len(body_lines) and body_lines[idx].strip() == '{':
                        pub_end = find_matching_brace(body_lines, idx)
                        pub_body = body_lines[idx + 1:pub_end]
                        idx = pub_end + 1
                    else:
                        pub_end = find_matching_brace(body_lines, idx - 1)
                        pub_body = body_lines[idx:pub_end]
                        idx = pub_end + 1

                # 转译 public 块内的每条语句
                for pline in pub_body:
                    pstripped = pline.strip()
                    if not pstripped or pstripped.startswith('//') or pstripped.startswith('#'):
                        continue
                    if pstripped == '}' or pstripped == '{':
                        continue
                    # 在 use.class 中，直接转译即可
                    parsed = parse_class_line(pstripped)
                    if parsed is None:
                        continue
                    out.append(f'{base_indent_str}{parsed}')
                continue

            idx += 1

        return out

    def parse_line(line):
        """解析单行代码，返回处理后的Python代码"""
        stripped = line.strip().rstrip(';')

        # 变量声明 v name = value
        m = re.match(r'v\s+(\w+)\s*=\s*(.+)', stripped)
        if m:
            return f'{m.group(1)} = {m.group(2)}'

        # pr 语句
        if stripped.startswith('pr '):
            return f'print({stripped[2:].strip()})'

        m = re.match(r'pr\s*\((.+)\)', stripped)
        if m:
            return f'print({m.group(1)})'

        # 其他语句
        if stripped.startswith('//') or stripped.startswith('#'):
            return stripped.replace('//', '#', 1)
        return (f'print("!! 转译错误: 无法识别的语句 -> {stripped}"); '
                f'raise Exception("无法识别的语句: {stripped}")')

    def make_window_to_python(block_lines, extra_indent=0, is_top_level=True):
        header = block_lines[0].strip()
        m = re.match(r'make\.window\s*\{\s*"([^"]*)"\s*\}\s*=>\s*\{', header)
        if not m:
            return [f'# Error: 窗口声明格式错误 -> {header}']

        title = m.group(1)
        base_indent = extra_indent
        indent_str = ' ' * base_indent

        code_lines = []
        code_lines.append(f'{indent_str}window = tk.Toplevel()')
        code_lines.append(f'{indent_str}window.title("{title}")')
        code_lines.append(f'{indent_str}window.geometry("400x300")')
        code_lines.append(f'{indent_str}window.configure(bg="#0a0a1a")')
        code_lines.append(
            f'{indent_str}canvas = tk.Canvas(window, bg="#0a0a1a", highlightthickness=0, width=400, height=300)')
        code_lines.append(
            f'{indent_str}scrollbar = tk.Scrollbar(window, orient="vertical", command=canvas.yview, bg="#1a1a2e", troughcolor="#0a0a1a", activebackground="#4a4a8a")')
        code_lines.append(
            f'{indent_str}canvas.configure(yscrollcommand=scrollbar.set)')
        code_lines.append(
            f'{indent_str}scrollbar.pack(side="right", fill="y")')
        code_lines.append(
            f'{indent_str}canvas.pack(side="left", fill="both", expand=True)')

        code_lines.append(f'{indent_str}def on_mousewheel(event):')
        code_lines.append(
            f'{indent_str}    canvas.yview_scroll(int(-1*(event.delta/120)), "units")')
        code_lines.append(f'{indent_str}def on_mousewheel_linux(event):')
        code_lines.append(
            f'{indent_str}    canvas.yview_scroll(-1 if event.num==4 else 1, "units")')
        code_lines.append(
            f'{indent_str}canvas.bind("<MouseWheel>", on_mousewheel)')
        code_lines.append(
            f'{indent_str}canvas.bind("<Button-4>", on_mousewheel_linux)')
        code_lines.append(
            f'{indent_str}canvas.bind("<Button-5>", on_mousewheel_linux)')

        # 收集所有的元素
        elements = []

        idx = 1
        while idx < len(block_lines):
            line = block_lines[idx].strip()
            if not line or line == '}':
                idx += 1
                continue

            if 'word(' in line:
                elements.append(('word', line, idx))
                idx += 1
                continue

            if line.startswith('button('):
                elements.append(('button', line, idx))
                idx += 1
                continue

            if line.startswith('picture('):
                elements.append(('picture', line, idx))
                idx += 1
                continue

            elements.append(('code', line, idx))
            idx += 1

        # 处理所有元素
        callbacks = []

        for elem_type, line, orig_idx in elements:
            if elem_type == 'code':
                parsed = parse_line(line)
                if parsed:
                    code_lines.append(f'{indent_str}{parsed}')

            elif elem_type == 'word':
                pattern_str = r'word\s*\(\s*"([^"]*)"\s*,\s*(\d+)\s*\[\s*(\d+)\s*,\s*(\d+)\s*\]\s*(?:,\s*"([^"]+)"\s*)?\)\s*;?'
                m = re.search(pattern_str, line)
                if m:
                    text, size, x, y, color = m.groups()
                    font = f'("Microsoft YaHei", {size}, "bold")'
                    fg_color = f'"{color}"' if color else '"#00ffcc"'
                    code_lines.append(
                        f'{indent_str}label = tk.Label(canvas, text="{text}", font={font}, bg="#0a0a1a", fg={fg_color})')
                    code_lines.append(
                        f'{indent_str}canvas.create_window({x}, {y}, window=label, anchor="center")')
                    continue

                start = line.find('(') + 1
                paren_count = 0
                end = start
                while end < len(line):
                    if line[end] == '(':
                        paren_count += 1
                    elif line[end] == ')':
                        paren_count -= 1
                    elif line[end] == ',' and paren_count == 0:
                        break
                    end += 1

                expr = line[start:end].strip()
                rest = line[end+1:].strip()
                pattern_rest = r'(\d+)\s*\[\s*(\d+)\s*,\s*(\d+)\s*\]\s*(?:,\s*"([^"]+)"\s*)?\)\s*;?'
                m2 = re.match(pattern_rest, rest)
                if m2:
                    size, x, y, color = m2.groups()
                    font = f'("Microsoft YaHei", {size}, "bold")'
                    fg_color = f'"{color}"' if color else '"#00ffcc"'
                    code_lines.append(
                        f'{indent_str}label = tk.Label(canvas, text={expr}, font={font}, bg="#0a0a1a", fg={fg_color})')
                    code_lines.append(
                        f'{indent_str}canvas.create_window({x}, {y}, window=label, anchor="center")')

            elif elem_type == 'button':
                pattern = r'button\s*\(\s*"([^"]*)"\s*,\s*(\d+)\s*\[\s*(\d+)\s*,\s*(\d+)\s*\]\s*(?:,\s*"([^"]+)"\s*)?\)\s*\{?'
                m = re.match(pattern, line)
                if not m:
                    continue

                text, size, x, y, color = m.groups()
                start_block = orig_idx + 1
                block_lines_inner, end_idx = get_block(start_block)
                cb_name = f"callback_{len(callbacks)}"
                cb_lines = []
                cb_lines.append(f'{indent_str}def {cb_name}():')

                sub_idx = 0
                while sub_idx < len(block_lines_inner):
                    bline = block_lines_inner[sub_idx].strip()
                    if not bline or bline == '}':
                        sub_idx += 1
                        continue

                    if bline.startswith('link('):
                        url_match = re.match(
                            r'link\s*\(\s*"([^"]*)"\s*\)\s*;?', bline)
                        if url_match:
                            url = url_match.group(1)
                            cb_lines.append(
                                f'{indent_str}    import webbrowser')
                            cb_lines.append(
                                f'{indent_str}    webbrowser.open("{url}")')
                        sub_idx += 1
                        continue

                    parsed = parse_line(bline)
                    if parsed:
                        cb_lines.append(f'{indent_str}    {parsed}')
                    sub_idx += 1

                callbacks.append(cb_lines)
                font = f'("Microsoft YaHei", {size}, "bold")'
                fg_color = f'"{color}"' if color else '"#00ffcc"'
                code_lines.append(f'{indent_str}btn = tk.Button(canvas, text="{text}", font={font}, bg="#1a1a3e", fg={fg_color}, activebackground="#2a2a5e", activeforeground="#66ffdd", relief="flat", bd=2, highlightthickness=1, highlightcolor="#00ffcc", command={cb_name})')
                code_lines.append(
                    f'{indent_str}canvas.create_window({int(x)}, {int(y)}, window=btn, anchor="center")')

            elif elem_type == 'picture':
                pattern = r'picture\s*\(\s*"([^"]*)"\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([0-9.]+)\s*)?\)\s*;?'
                m = re.match(pattern, line)
                if m:
                    path, width, height, opacity = m.groups()
                    opacity = opacity or '1.0'
                    code_lines.append(f'{indent_str}try:')
                    code_lines.append(
                        f'{indent_str}    from PIL import Image, ImageTk')
                    code_lines.append(f'{indent_str}except ImportError:')
                    code_lines.append(
                        f'{indent_str}    print("Pillow 未安装，无法显示图片")')
                    code_lines.append(f'{indent_str}    raise')
                    code_lines.append(
                        f'{indent_str}img = Image.open(r"{path}")')
                    code_lines.append(
                        f'{indent_str}img = img.resize(({width}, {height}))')
                    code_lines.append(
                        f'{indent_str}photo = ImageTk.PhotoImage(img)')
                    code_lines.append(
                        f'{indent_str}canvas.create_image(0, 0, image=photo, anchor="nw")')
                    code_lines.append(f'{indent_str}canvas.image = photo')

        # 添加回调函数
        for cb_lines in callbacks:
            code_lines.extend(cb_lines)
            code_lines.append('')

        code_lines.append(f'{indent_str}canvas.update_idletasks()')
        code_lines.append(
            f'{indent_str}canvas.config(scrollregion=canvas.bbox("all"))')
        return code_lines

    while i < total_lines:
        raw = lines[i]
        stripped = raw.strip()

        if stripped.startswith('//'):
            output.append(INDENT * indent + stripped.replace('//', '#', 1))
            i += 1
            continue

        if stripped.startswith('/') and not stripped.startswith('//'):
            output.append(INDENT * indent + stripped[1:].strip())
            i += 1
            continue

        if stripped.startswith('#translate'):
            match = re.match(r'#translate\s*<\s*([^>]+)\s*>\s*join', stripped)
            if match:
                lib_name = match.group(1).strip()
                pending_imports.append(lib_name)
                i += 1
                continue
            else:
                output.append(INDENT * indent +
                              f'print("!! 转译错误: 无效的导入格式 -> {stripped}")')
                output.append(INDENT * indent +
                              f'print("!! 正确格式: #translate<库名>join")')
                i += 1
                continue

        if stripped.startswith('#appreciated'):
            if pending_imports:
                for lib in pending_imports:
                    if lib.lower() == 'pythoncode':
                        python_code_allowed = True
                    else:
                        output.append(INDENT * indent + f'import {lib}')
                pending_imports = []
            i += 1
            continue

        if pending_imports and not skip_politeness:
            if not stripped.startswith('#') and not stripped.startswith('//'):
                output.append(
                    INDENT * indent + f'print("!! ERROR: You are impolite. How can I let you import the code repository?")')
                output.append(
                    INDENT * indent + f'print("!! 你请求导入以下库但没有批准: {", ".join(pending_imports)}")')
                output.append(INDENT * indent +
                              f'print("!! 请在代码中使用 #appreciated 批准导入")')
                output.append(INDENT * indent +
                              f'raise Exception("导入未批准: 请先使用 #appreciated")')
                pending_imports = []
                i += 1
                continue
        skip_politeness = False

        if stripped == '#sorry':
            i += 1
            continue

        if stripped.startswith('#'):
            emphasized = stripped[1:].strip()
            if emphasized and not emphasized.startswith('#'):
                for mod in re.findall(r'([a-zA-Z_]\w*)\s*\.', emphasized):
                    if mod in AUTO_IMPORTABLE and mod not in auto_imported:
                        auto_imported.add(mod)
                        output.append(INDENT * indent + f'import {mod}')
                lines[i] = emphasized
                skip_politeness = True
                continue
            i += 1
            continue

        if stripped.startswith('code.python'):
            if not re.match(r'code\.python\s*\{', stripped):
                output.append(INDENT * indent +
                              'print("!! 转译错误: code.python 需要以 { 开头")')
                i += 1
                continue
            if not python_code_allowed:
                output.append(
                    INDENT * indent + 'print("!! 错误: 使用 code.python 需要先输入 #translate<Pythoncode>join 和 #appreciated 批准")')
                output.append(
                    INDENT * indent + 'raise Exception("code.python 未批准: 请先使用 #translate<Pythoncode>join 和 #appreciated")')
                i += 1
                continue
            block_lines, end_idx = get_block(i)
            body = block_lines[1:-1]
            base = None
            for bl in body:
                if bl.strip():
                    base = len(bl) - len(bl.lstrip())
                    break
            for bl in body:
                if not bl.strip():
                    continue
                content = bl[base:] if base is not None else bl.strip()
                output.append(INDENT * indent + content)
            i = end_idx + 1
            continue

        if stripped.startswith('pr '):
            content = stripped[2:].strip()
            output.append(INDENT * indent + f'print({content})')
            i += 1
            continue
        m = re.match(r'pr\s*\((.+)\)', stripped)
        if m:
            content = m.group(1)
            output.append(INDENT * indent + f'print({content})')
            i += 1
            continue

        m = re.match(r'v\s+(\w+)\s*=\s*new\s+(\w+)\s*\((.*)\)\s*$', stripped)
        if m:
            var_name = m.group(1)
            class_name = m.group(2)
            args = m.group(3)
            output.append(INDENT * indent +
                          f'{var_name} = {class_name}({args})')
            i += 1
            continue

        m = re.match(r'v\s+(\w+)\s*=\s*(.+)', stripped)
        if m:
            var_name = m.group(1)
            value = m.group(2)
            output.append(INDENT * indent + f'{var_name} = {value}')
            i += 1
            continue

        m = re.match(r'd\s+(\w+)\s*\(([^)]*)\)\s*\{', stripped)
        if m:
            func_name = m.group(1)
            params = m.group(2)
            output.append(INDENT * indent + f'def {func_name}({params}):')
            indent += 1
            i += 1
            continue

        if stripped.startswith('rz '):
            value = stripped[2:].strip()
            next_line = lines[i + 1].strip() if i + 1 < total_lines else ''
            if len(value) > LARGE_RETURN_THRESHOLD:
                if next_line != '#sorry':
                    output.append(INDENT * indent +
                                  'print("!! 不礼貌: 返回值过大但没有道歉!")')
                    output.append(INDENT * indent +
                                  'print("!! 请在 rz 语句的下一行添加 #sorry 表示歉意")')
                    output.append(
                        INDENT * indent + 'raise Exception("返回值过大未道歉: 请在 rz 下一行添加 #sorry")')
                else:
                    i += 1
            output.append(INDENT * indent + f'return {value}')
            i += 1
            continue

        if stripped.startswith('if '):
            condition = stripped[2:].strip()
            output.append(INDENT * indent + f'if {condition}:')
            indent += 1
            i += 1
            continue

        if stripped == 'es':
            indent = max(0, indent - 1)
            output.append(INDENT * indent + 'else:')
            indent += 1
            i += 1
            continue

        m = re.match(r'from\s+to\s*\((.+?)\)\s*\{', stripped)
        if m:
            condition = m.group(1).strip()
            if condition in ('false', 'False'):
                output.append(INDENT * indent + 'if False:')
            else:
                output.append(INDENT * indent + f'while {condition}:')
            indent += 1
            i += 1
            continue

        # ====== class 块定义 ======
        if re.match(r'class\s+\w+\s*\{', stripped):
            block_lines, end_idx = get_block(i)
            class_code = parse_class_body_to_python(
                block_lines, INDENT * indent)
            for line in class_code:
                output.append(line)
            i = end_idx + 1
            continue

        # ====== new 类名(参数) 对象实例化（不带 v 前缀） ======
        m = re.match(r'(\w+)\s*=\s*new\s+(\w+)\s*\((.*)\)\s*$', stripped)
        if m:
            var_name = m.group(1)
            class_name = m.group(2)
            args = m.group(3)
            output.append(INDENT * indent +
                          f'{var_name} = {class_name}({args})')
            i += 1
            continue

        # ====== use.class(对象名) { public { ... } } 块 ======
        if re.match(r'use\.class\s*\(\s*\w+\s*\)\s*\{', stripped):
            block_lines, end_idx = get_block(i)
            use_class_code = transpile_use_class_block(
                block_lines, INDENT * indent)
            for line in use_class_code:
                output.append(line)
            i = end_idx + 1
            continue

        if stripped.startswith('make.window'):
            block_lines, end_idx = get_block(i)
            window_code = make_window_to_python(
                block_lines, extra_indent=indent * 4, is_top_level=True)
            for line in window_code:
                if 'mainloop' in line and 'window' in line:
                    continue
                output.append(line)
            i = end_idx + 1
            continue

        if stripped == '}':
            indent = max(0, indent - 1)
            i += 1
            continue

        if stripped:
            if stripped.startswith('#') or stripped.startswith('//'):
                output.append(INDENT * indent + stripped.replace('//', '#', 1))
            else:
                output.append(INDENT * indent +
                              f'print("!! 转译错误: 无法识别的语句 -> {stripped}")')
                output.append(INDENT * indent +
                              f'raise Exception("无法识别的语句: {stripped}")')
        i += 1

    return '\n'.join(output)


class WindowscriptIDE:
    def __init__(self, root):
        self.root = root
        self.root.title(">> Windowscript IDE 2.1.1-20260827-17 <<")
        self.root.geometry("950x700")
        self.root.configure(bg="#05050f")
        self.current_file_path = None
        self.is_running = False
        self.last_saved_content = ""

        self.script_full_path = os.path.abspath(__file__)
        self.ide_path = os.path.dirname(self.script_full_path)
        self.script_name = os.path.basename(self.script_full_path)

        self.icon_path = os.path.join(self.ide_path, 'icon', 'icon.ico')
        try:
            root.iconbitmap(default=self.icon_path)
        except:
            pass

        self._setup_style()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        toolbar = tk.Frame(root, bg="#0a0a1a", height=50)
        toolbar.pack(side=tk.TOP, fill=tk.X, pady=(0, 2))

        line_frame = tk.Frame(root, bg="#00ffcc", height=2)
        line_frame.pack(side=tk.TOP, fill=tk.X)

        logo_label = tk.Label(toolbar, text="[ WINDOWSCRIPT ]", font=("Consolas", 14, "bold"),
                              bg="#0a0a1a", fg="#00ffcc")
        logo_label.pack(side=tk.LEFT, padx=15)

        sep = tk.Label(toolbar, text="|", bg="#0a0a1a", fg="#2a2a5a")
        sep.pack(side=tk.LEFT, padx=5)

        btn_style = {
            "bg": "#0d0d2b",
            "fg": "#66ffdd",
            "font": ("Consolas", 10),
            "relief": "flat",
            "bd": 1,
            "highlightthickness": 1,
            "highlightcolor": "#00ffcc",
            "highlightbackground": "#1a1a3e",
            "activebackground": "#1a1a4e",
            "activeforeground": "#00ffcc",
            "width": 10,
            "pady": 3
        }

        btn_open = tk.Button(
            toolbar, text="[O] 打开", command=self.open_file, **btn_style)
        btn_open.pack(side=tk.LEFT, padx=2)

        btn_save = tk.Button(
            toolbar, text="[S] 保存", command=self.save_file, **btn_style)
        btn_save.pack(side=tk.LEFT, padx=2)

        btn_save_as = tk.Button(
            toolbar, text="[A] 另存为", command=self.save_as_file, **btn_style)
        btn_save_as.pack(side=tk.LEFT, padx=2)

        sep2 = tk.Label(toolbar, text="|", bg="#0a0a1a", fg="#2a2a5a")
        sep2.pack(side=tk.LEFT, padx=5)

        btn_run = tk.Button(toolbar, text="[>] 运行", command=self.run_code,
                            bg="#003322", fg="#00ffcc", font=("Consolas", 10, "bold"),
                            relief="flat", bd=1, highlightthickness=1,
                            highlightcolor="#00ffcc", highlightbackground="#00ffcc",
                            activebackground="#004433", activeforeground="#66ffdd",
                            width=10, pady=3)
        btn_run.pack(side=tk.LEFT, padx=2)

        status_frame = tk.Frame(toolbar, bg="#0a0a1a")
        status_frame.pack(side=tk.RIGHT, padx=15)

        self.status_label = tk.Label(status_frame, text="* SYSTEM READY",
                                     font=("Consolas", 9), bg="#0a0a1a", fg="#4488aa")
        self.status_label.pack(side=tk.LEFT, padx=5)

        self.version_label = tk.Label(status_frame, text=f"v{VERSION}",
                                      font=("Consolas", 9), bg="#0a0a1a", fg="#2a5a5a")
        self.version_label.pack(side=tk.LEFT, padx=5)

        editor_frame = tk.Frame(root, bg="#05050f")
        editor_frame.pack(side=tk.TOP, fill=tk.BOTH,
                          expand=True, padx=8, pady=5)

        editor_label = tk.Label(editor_frame, text=">> CODE MATRIX <<",
                                font=("Consolas", 10, "bold"), bg="#05050f", fg="#00ffcc")
        editor_label.pack(side=tk.TOP, anchor="w", padx=2, pady=(0, 3))

        self.code_editor = scrolledtext.ScrolledText(
            editor_frame, font=("Consolas", 12),
            bg="#0a0a15", fg="#66ddcc", insertbackground="#00ffcc",
            relief="flat", bd=2, highlightthickness=1,
            highlightcolor="#00ffcc", highlightbackground="#1a1a3e",
            undo=True, wrap=tk.NONE
        )
        self.code_editor.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.code_editor.tag_configure("comment", foreground="#337766")
        self.code_editor.tag_configure("keyword", foreground="#66ddff")
        self.code_editor.tag_configure("string", foreground="#88dd88")
        self.code_editor.tag_configure("function", foreground="#ff9966")

        self.code_editor.bind('<Tab>', self.insert_tab)
        self.code_editor.bind('<Shift-Tab>', self.remove_tab)
        self.code_editor.bind('<BackSpace>', self.on_backspace)
        self.code_editor.bind('<KeyRelease>', self.on_key_release)

        output_frame = tk.Frame(root, bg="#05050f")
        output_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=5)

        output_header = tk.Frame(output_frame, bg="#05050f")
        output_header.pack(side=tk.TOP, fill=tk.X)

        output_label = tk.Label(output_header, text=">> TERMINAL OUTPUT <<",
                                font=("Consolas", 10, "bold"), bg="#05050f", fg="#4488aa")
        output_label.pack(side=tk.LEFT)

        self.path_label = tk.Label(output_header, text="",
                                   font=("Consolas", 9), bg="#05050f", fg="#335555")
        self.path_label.pack(side=tk.RIGHT)

        self.output_area = scrolledtext.ScrolledText(
            output_frame, font=("Consolas", 10),
            height=8, bg="#080818", fg="#66ddcc",
            relief="flat", bd=2, highlightthickness=1,
            highlightcolor="#1a3a3a", highlightbackground="#0a1a1a"
        )
        self.output_area.pack(side=tk.TOP, fill=tk.BOTH,
                              expand=True, pady=(2, 0))

        self.output_area.tag_configure("success", foreground="#00ff88")
        self.output_area.tag_configure("error", foreground="#ff4466")
        self.output_area.tag_configure("info", foreground="#4488aa")
        self.output_area.tag_configure("path", foreground="#66aaff")
        self.output_area.tag_configure("version", foreground="#ff9966")
        self.output_area.tag_configure("idepath", foreground="#88dd88")

        self.root.bind('<Control-s>', lambda event: self.save_file())
        self.root.bind('<Control-S>', lambda event: self.save_file())
        self.root.bind('<Control-o>', lambda event: self.open_file())
        self.root.bind('<F5>', lambda event: self.run_code())

        self.code_editor.insert("1.0", "")
        self.last_saved_content = ""

    def _setup_style(self):
        try:
            self.root.attributes('-alpha', 0.98)
        except:
            pass

    def show_warning_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("来自ESC工作室的警告")
        try:
            dialog.iconbitmap(default=self.icon_path)
        except:
            pass
        dialog.configure(bg="#0a0a1a")
        dialog.resizable(False, False)

        msg = tk.Label(dialog, text="您当前正在使用测试版本，建议使用稳定版，否则后果自负",
                       font=("Microsoft YaHei", 11), bg="#0a0a1a", fg="#ffcc66",
                       wraplength=440, justify="center")
        msg.pack(padx=30, pady=(30, 15))

        btn_ok = tk.Button(dialog, text="确定", command=dialog.destroy,
                           bg="#003322", fg="#00ffcc", font=("Microsoft YaHei", 10, "bold"),
                           relief="flat", bd=1, width=10, pady=3,
                           activebackground="#004433", activeforeground="#66ffdd")
        btn_ok.pack(pady=(0, 20))

        dialog.update_idletasks()
        w, h = dialog.winfo_reqwidth(), dialog.winfo_reqheight()
        x = (dialog.winfo_screenwidth() - w) // 2
        y = (dialog.winfo_screenheight() - h) // 3
        dialog.geometry(f"{w}x{h}+{x}+{y}")

        dialog.transient(self.root)
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.update()
        dialog.grab_set()
        self.root.wait_window(dialog)

    def is_modified(self):
        current_content = self.code_editor.get("1.0", tk.END).rstrip('\n')
        return current_content != self.last_saved_content

    def on_closing(self):
        if self.is_modified():
            response = messagebox.askyesnocancel("保存文件", "内容已修改，是否保存？")
            if response is True:
                if self.save_file():
                    self.root.destroy()
            elif response is False:
                self.root.destroy()
        else:
            self.root.destroy()

    def insert_tab(self, event):
        self.code_editor.insert(tk.INSERT, "    ")
        return "break"

    def remove_tab(self, event):
        row, col = map(int, self.code_editor.index(tk.INSERT).split('.'))
        line_start = f"{row}.0"
        line_text = self.code_editor.get(line_start, f"{row}.end")
        if line_text.startswith("    "):
            self.code_editor.delete(line_start, f"{row}.4")
        return "break"

    def on_backspace(self, event):
        try:
            if self.code_editor.tag_ranges('sel'):
                self.code_editor.delete('sel.first', 'sel.last')
                return "break"

            current_pos = self.code_editor.index(tk.INSERT)
            if current_pos.endswith('.0'):
                return "break"

            prev_pos = self.code_editor.index(f"{current_pos} - 1 char")
            char_to_delete = self.code_editor.get(prev_pos, current_pos)
            if char_to_delete and '\uDC00' <= char_to_delete <= '\uDFFF':
                prev_pos = self.code_editor.index(f"{prev_pos} - 1 char")
            self.code_editor.delete(prev_pos, current_pos)

            return "break"
        except Exception as e:
            return None

    def on_key_release(self, event):
        pass

    def save_as_file(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".ws",
            filetypes=[("Windowscript 文件", "*.ws"),
                       ("文本文件", "*.txt"), ("所有文件", "*.*")],
            title=">> 保存 Windowscript 文件 <<"
        )
        if not path:
            return False
        self.current_file_path = path
        self._write_to_file(path)
        self.path_label.config(text=f"[FILE] {os.path.basename(path)}")
        self.status_label.config(text="+ 文件已保存", fg="#00ff88")
        self.last_saved_content = self.code_editor.get(
            "1.0", tk.END).rstrip('\n')
        messagebox.showinfo("成功", f"文件已保存：{os.path.basename(path)}")
        return True

    def save_file(self):
        if self.current_file_path is None:
            return self.save_as_file()
        else:
            self._write_to_file(self.current_file_path)
            self.path_label.config(
                text=f"[FILE] {os.path.basename(self.current_file_path)}")
            self.status_label.config(text="+ 文件已保存", fg="#00ff88")
            self.last_saved_content = self.code_editor.get(
                "1.0", tk.END).rstrip('\n')
            return True

    def _write_to_file(self, path):
        content = self.code_editor.get("1.0", tk.END).rstrip('\n')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)

    def open_file(self):
        if self.is_modified():
            response = messagebox.askyesnocancel("保存文件", "当前内容已修改，是否先保存？")
            if response is True:
                if not self.save_file():
                    return
            elif response is None:
                return

        path = filedialog.askopenfilename(
            filetypes=[("Windowscript 文件", "*.ws"),
                       ("文本文件", "*.txt"), ("所有文件", "*.*")],
            title=">> 打开 Windowscript 文件 <<"
        )
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            self.code_editor.delete("1.0", tk.END)
            self.code_editor.insert("1.0", content)
            self.current_file_path = path
            self.path_label.config(text=f"[FILE] {os.path.basename(path)}")
            self.status_label.config(text="+ 文件已加载", fg="#00ff88")
            self.last_saved_content = content.rstrip('\n')
            messagebox.showinfo("成功", f"已打开：{os.path.basename(path)}")
        except Exception as e:
            messagebox.showerror("错误", f"打开文件失败：{str(e)}")

    def run_code(self):
        if self.is_running:
            return

        if self.current_file_path is None:
            path = filedialog.asksaveasfilename(
                defaultextension=".ws",
                filetypes=[("Windowscript 文件", "*.ws")],
                title=">> 运行前请保存文件 <<"
            )
            if not path:
                return
            self.current_file_path = path
            self._write_to_file(path)
            self.path_label.config(text=f"[FILE] {os.path.basename(path)}")
            self.last_saved_content = self.code_editor.get(
                "1.0", tk.END).rstrip('\n')

        self.is_running = True
        self.output_area.delete("1.0", tk.END)

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.output_area.insert(tk.END, "+" + "="*50 + "+\n", "info")
        self.output_area.insert(
            tk.END, f"|  >> WINDOWSCRIPT EXECUTION ENGINE v{VERSION} <<  |\n", "info")
        self.output_area.insert(tk.END, "+" + "="*50 + "+\n", "info")
        self.output_area.insert(tk.END, f"\n>> 启动时间: {timestamp}\n", "info")
        self.output_area.insert(
            tk.END, f">> 文件路径: {self.current_file_path}\n", "path")
        self.output_area.insert(
            tk.END, f">> Windowscript版本: {VERSION}\n", "version")
        self.output_area.insert(
            tk.END, f">> Windowscript所在路径: {self.ide_path}\n", "idepath")
        self.output_area.insert(
            tk.END, f">> Windowscript文件名: {self.script_name}\n\n", "idepath")

        original_code = self.code_editor.get("1.0", tk.END)

        try:
            python_code = transpile(original_code)
        except Exception as e:
            self.output_area.insert(tk.END, f"\n!! 转译错误：{str(e)}\n", "error")
            self.is_running = False
            self.status_label.config(text="! 运行失败", fg="#ff4466")
            return

        old_stdout = sys.stdout
        sys.stdout = captured_output = io.StringIO()

        import tkinter as tk_module
        exec_globals = {
            '__builtins__': __builtins__,
            'tk': tk_module,
            'print': print,
        }

        self.status_label.config(text="> 运行中...", fg="#66ddff")

        try:
            exec(python_code, exec_globals)
        except Exception as e:
            self.output_area.insert(tk.END, f"\n!! 运行时错误：{str(e)}\n", "error")
        else:
            output = captured_output.getvalue()
            if output:
                self.output_area.insert(tk.END, "\n>> 程序输出：\n", "info")
                self.output_area.insert(tk.END, output, "success")
            else:
                self.output_area.insert(tk.END, "\n>> 程序执行完成（无输出）\n", "info")
        finally:
            sys.stdout = old_stdout

        self.output_area.insert(tk.END, f"\n>> 执行完成 [{timestamp}]\n", "info")
        self.output_area.insert(tk.END, "-"*50 + "\n", "info")

        try:
            self.output_area.see(tk.END)
        except tk.TclError:
            pass

        self.is_running = False
        self.status_label.config(text="+ 运行完成", fg="#00ff88")


if __name__ == "__main__":
    root = tk.Tk()
    app = WindowscriptIDE(root)
    app.show_warning_dialog()
    root.mainloop()
