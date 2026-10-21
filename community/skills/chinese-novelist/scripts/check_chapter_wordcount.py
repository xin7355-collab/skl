#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章节字数检查脚本
检查指定章节文件的字数，低于3000字时提示需要扩充

用法:
  python check_chapter_wordcount.py <章节文件路径> [最小字数] [--json]
  python check_chapter_wordcount.py --all <目录路径> [最小字数] [--json]

退出码: 0=全部达标  1=有章节不达标  2=文件/目录/参数错误
--json: 输出机器可读 JSON（供 Agent 解析写回 02-写作计划.json）；
        出错时输出 {"error": "..."}；summary.errors 为文件缺失数
"""

import json
import os
import re
import sys
from pathlib import Path

# 修复 Windows 控制台编码问题
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


def count_chinese_words(text: str) -> int:
    """统计中文字数（排除标点符号和Markdown标记）"""
    # 移除Markdown标记
    text = re.sub(r'#{1,6}\s*', '', text)  # 标题
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)  # 粗体
    text = re.sub(r'\*(.*?)\*', r'\1', text)  # 斜体
    text = re.sub(r'~~(.*?)~~', r'\1', text)  # 删除线
    text = re.sub(r'`(.*?)`', r'\1', text)  # 行内代码
    text = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', text)  # 链接

    # 统计中文字符（汉字）
    chinese_chars = re.findall(r'[\u4e00-\u9fff]', text)
    return len(chinese_chars)


def extract_content_from_chapter(file_path: Path) -> str:
    """从章节文件中提取正文内容（排除标题等元数据）"""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 查找正文开始位置（通常是第一个一级标题或二级标题之后）
    lines = content.split('\n')

    # 跳过开头的元数据（如 # 第XX章 标题）
    content_start = 0
    for i, line in enumerate(lines):
        if line.startswith('#') and '章' in line:
            content_start = i + 1
            break

    # 提取正文
    main_content = '\n'.join(lines[content_start:])
    return main_content


def check_chapter(file_path: str, min_words: int = 3000) -> dict:
    """检查单个章节的字数"""
    path = Path(file_path)

    if not path.exists():
        return {
            'file': str(path),
            'exists': False,
            'word_count': 0,
            'status': 'error',
            'message': f'文件不存在: {file_path}'
        }

    main_content = extract_content_from_chapter(path)
    word_count = count_chinese_words(main_content)

    status = 'pass' if word_count >= min_words else 'fail'
    message = f'字数: {word_count}' + (
        f' (✓ 达标)' if word_count >= min_words else f' (✗ 不足，需要至少 {min_words} 字)'
    )

    return {
        'file': str(path),
        'exists': True,
        'word_count': word_count,
        'status': status,
        'message': message
    }


def chapter_sort_key(path: Path):
    """按章节号数值排序（避免 第10章 排在 第2章 前面的字符串序问题）"""
    m = re.search(r'第(\d+)章', path.name)
    return (int(m.group(1)) if m else 9999, path.name)


def check_all_chapters(directory: str, pattern: str = '第*.md', min_words: int = 3000):
    """检查目录下所有符合模式的章节文件，返回 (results, error_message)"""
    dir_path = Path(directory)
    if not dir_path.exists():
        return [], f'目录不存在 - {directory}'
    if not dir_path.is_dir():
        return [], f'路径不是目录 - {directory}'

    chapter_files = sorted(dir_path.glob(pattern), key=chapter_sort_key)
    if not chapter_files:
        return [], f'目录下没有找到章节文件（模式: {pattern}）'

    results = [check_chapter(str(chapter_file), min_words) for chapter_file in chapter_files]
    return results, None


def print_results(results: list, min_words: int = 3000):
    """打印检查结果"""
    if not results:
        print('没有找到章节文件')
        return

    total_words = 0
    passed = 0
    failed = 0
    errors = sum(1 for r in results if not r['exists'])

    print('\n' + '=' * 60)
    print('章节字数检查报告')
    print('=' * 60)

    for result in results:
        if not result['exists']:
            print(f'\n❌ {result["file"]}')
            print(f'   {result["message"]}')
            continue

        total_words += result['word_count']
        if result['status'] == 'pass':
            passed += 1
            icon = '✅'
        else:
            failed += 1
            icon = '⚠️ '

        print(f'\n{icon} {Path(result["file"]).name}')
        print(f'   {result["message"]}')

    print('\n' + '-' * 60)
    print(f'总计: {len(results)} 章 | {passed} 章达标 | {failed} 章不足 | {errors} 章文件错误 | 总字数: {total_words:,}')
    print('-' * 60)

    if failed > 0:
        print(f'\n⚠️  有 {failed} 章内容不足 {min_words} 字，建议使用扩充技巧:')
        print('   - 添加细节描写（环境、心理、动作）')
        print('   - 增加对话场景')
        print('   - 扩展人物内心活动')
        print('   - 补充背景故事')
        print(f'\n   参考: references/guides/content-expansion.md')


def main():
    """主函数"""
    args = [a for a in sys.argv[1:] if a != '--json']
    as_json = '--json' in sys.argv[1:]

    def emit_error(msg: str):
        """按输出模式打印错误并退出（退出码 2 = 文件/目录/参数错误）"""
        print(json.dumps({'error': msg}, ensure_ascii=False) if as_json else msg)
        sys.exit(2)

    def parse_min_words(raw: str) -> int:
        try:
            return int(raw)
        except ValueError:
            emit_error(f'最小字数必须是整数，收到 "{raw}"')

    min_words = 3000

    # skl 修改：--help 原本被當成章節檔名、回報「文件不存在」並結束碼 2
    want_help = args[:1] in (['-h'], ['--help'])
    if not args or want_help:
        if as_json and not want_help:
            print(json.dumps({'error': 'no arguments'}, ensure_ascii=False))
            sys.exit(2)
        print('用法:')
        print('  检查单个章节: python check_chapter_wordcount.py <章节文件路径> [最小字数] [--json]')
        print('  检查所有章节: python check_chapter_wordcount.py --all <目录路径> [最小字数] [--json]')
        print('')
        print('退出码: 0=全部达标  1=有章节不达标  2=文件/目录错误')
        print('')
        print('示例:')
        print('  python check_chapter_wordcount.py novels/故事/第01章.md')
        print('  python check_chapter_wordcount.py novels/故事/第01章.md 3500 --json')
        print('  python check_chapter_wordcount.py --all novels/故事 --json')
        sys.exit(0 if want_help else 2)

    if args[0] == '--all':
        if len(args) < 2:
            emit_error('错误: 使用 --all 时需要指定目录路径')
        directory = args[1]
        min_words = parse_min_words(args[2]) if len(args) > 2 else 3000
        results, error = check_all_chapters(directory, min_words=min_words)
        if error:
            emit_error(f'错误: {error}')
        has_error = any(not r['exists'] for r in results)
    else:
        file_path = args[0]
        min_words = parse_min_words(args[1]) if len(args) > 1 else 3000
        results = [check_chapter(file_path, min_words)]
        has_error = not results[0]['exists']

    if as_json:
        passed = sum(1 for r in results if r['exists'] and r['status'] == 'pass')
        failed = sum(1 for r in results if r['exists'] and r['status'] == 'fail')
        errors = sum(1 for r in results if not r['exists'])
        output = {
            'results': results,
            'summary': {
                'total': len(results),
                'passed': passed,
                'failed': failed,
                'errors': errors,
                'totalWords': sum(r['word_count'] for r in results),
                'minWords': min_words,
            },
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print_results(results, min_words)

    # 退出码: 2=文件/目录错误, 1=有章节不达标, 0=全部达标
    if has_error:
        sys.exit(2)
    if any(r['exists'] and r['status'] == 'fail' for r in results):
        sys.exit(1)
    sys.exit(0)


if __name__ == '__main__':
    main()
