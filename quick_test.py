#!/usr/bin/env python3
"""Report 工作台快速功能测试"""

import requests
import json
import time
from datetime import datetime

BASE_URL = "http://localhost:8000"
COLORS = {
    'green': '\033[92m',
    'red': '\033[91m',
    'yellow': '\033[93m',
    'blue': '\033[94m',
    'end': '\033[0m'
}

def print_header(text):
    print(f"\n{'='*60}")
    print(f"{COLORS['blue']}{text}{COLORS['end']}")
    print('='*60)

def print_test(name, passed, details=""):
    status = f"{COLORS['green']}✅ PASS{COLORS['end']}" if passed else f"{COLORS['red']}❌ FAIL{COLORS['end']}"
    print(f"{status} | {name}")
    if details:
        print(f"       {details}")

def test_api_routes():
    """测试 API 路由是否正常"""
    print_header("测试 1: API 路由检查")
    
    try:
        # 测试路由列表接口
        resp = requests.get(f"{BASE_URL}/api/report-workbench/routes", timeout=5)
        passed = resp.status_code == 200
        
        if passed:
            routes = resp.json()
            print_test("路由列表接口", True, f"找到 {len(routes)} 个模板")
            
            # 检查关键路由
            route_ids = [r['id'] for r in routes]
            for route_id in ['lumbar_mri', 'cervical_mri', 'brain_mri']:
                exists = route_id in route_ids
                print_test(f"  - {route_id}", exists)
        else:
            print_test("路由列表接口", False, f"状态码: {resp.status_code}")
            
        return passed
    except Exception as e:
        print_test("路由列表接口", False, str(e))
        return False

def test_report_generation():
    """测试报告生成功能"""
    print_header("测试 2: 报告生成功能")
    
    test_cases = [
        {
            "name": "腰椎 MRI",
            "input": "腰4/5椎间盘向后中央突出，硬膜囊受压，椎管狭窄",
            "expected_route": "lumbar_mri"
        },
        {
            "name": "颈椎 MRI",
            "input": "C5/6椎间盘向后膨出，脊髓受压",
            "expected_route": "cervical_mri"
        },
        {
            "name": "颅脑 MRI",
            "input": "右侧额叶占位性病变，周围水肿",
            "expected_route": "brain_mri"
        }
    ]
    
    all_passed = True
    
    for case in test_cases:
        print(f"\n{COLORS['yellow']}测试案例: {case['name']}{COLORS['end']}")
        conv_id = f"test_{case['expected_route']}_{int(time.time())}"
        
        try:
            url = f"{BASE_URL}/api/report-workbench/sessions/{conv_id}/generate"
            params = {
                "input_text": case["input"],
                "language": "zh-CN"
            }
            
            # 使用 SSE 连接
            resp = requests.get(url, params=params, stream=True, timeout=30)
            
            if resp.status_code != 200:
                print_test(f"  生成请求", False, f"状态码: {resp.status_code}")
                all_passed = False
                continue
            
            # 解析 SSE 事件
            events = {}
            for line in resp.iter_lines():
                if line:
                    line_str = line.decode('utf-8')
                    
                    if line_str.startswith('event:'):
                        event_type = line_str[6:].strip()
                    elif line_str.startswith('data:'):
                        data_str = line_str[5:].strip()
                        try:
                            data = json.loads(data_str)
                            events[event_type] = data
                        except:
                            pass
            
            # 验证路由识别
            if 'route_resolved' in events:
                route_data = events['route_resolved']
                route_correct = route_data.get('route_id') == case['expected_route']
                print_test(f"  路由识别", route_correct, 
                          f"识别为: {route_data.get('route_name', 'unknown')}")
            else:
                print_test(f"  路由识别", False, "未收到 route_resolved 事件")
                all_passed = False
            
            # 验证报告生成
            if 'report_updated' in events:
                report = events['report_updated'].get('report', {})
                has_findings = bool(report.get('imaging_findings'))
                has_diagnosis = bool(report.get('imaging_diagnosis'))
                
                print_test(f"  影像所见章节", has_findings,
                          f"{len(report.get('imaging_findings', ''))} 字符" if has_findings else "")
                print_test(f"  影像诊断章节", has_diagnosis,
                          f"{len(report.get('imaging_diagnosis', ''))} 字符" if has_diagnosis else "")
                
                # 检查禁用词
                forbidden_words = ['待补充', 'XXX', '未提供', '暂无']
                full_text = report.get('imaging_findings', '') + report.get('imaging_diagnosis', '')
                has_forbidden = any(word in full_text for word in forbidden_words)
                print_test(f"  无占位词", not has_forbidden)
                
                all_passed = all_passed and has_findings and has_diagnosis and not has_forbidden
            else:
                print_test(f"  报告生成", False, "未收到 report_updated 事件")
                all_passed = False
            
            # 验证追问
            if 'report_questions' in events:
                questions = events['report_questions'].get('questions', [])
                valid_count = len(questions) <= 3
                print_test(f"  追问数量限制", valid_count, f"{len(questions)} 个追问")
            
            time.sleep(1)  # 避免请求过快
            
        except Exception as e:
            print_test(f"  生成流程", False, str(e))
            all_passed = False
    
    return all_passed

def test_frontend():
    """测试前端资源"""
    print_header("测试 3: 前端资源检查")
    
    resources = [
        ("/", "主页面"),
        ("/app.js", "JavaScript"),
        ("/style.css", "样式表"),
    ]
    
    all_passed = True
    for path, name in resources:
        try:
            resp = requests.get(f"{BASE_URL}{path}", timeout=5)
            passed = resp.status_code == 200
            print_test(name, passed, f"{len(resp.content)} 字节")
            all_passed = all_passed and passed
        except Exception as e:
            print_test(name, False, str(e))
            all_passed = False
    
    # 检查 app.js 中的关键函数
    try:
        resp = requests.get(f"{BASE_URL}/app.js", timeout=5)
        js_content = resp.text
        
        functions = [
            "initReportWorkbench",
            "generateReport",
            "iterateReport",
            "copyReportSection",
            "saveReport",
            "exportReport"
        ]
        
        for func in functions:
            exists = f"function {func}" in js_content or f"const {func}" in js_content
            print_test(f"  函数 {func}", exists)
            all_passed = all_passed and exists
            
    except Exception as e:
        print_test("JavaScript 函数检查", False, str(e))
        all_passed = False
    
    return all_passed

def test_html_structure():
    """测试 HTML 结构"""
    print_header("测试 4: HTML 结构检查")
    
    try:
        resp = requests.get(f"{BASE_URL}/", timeout=5)
        html = resp.text
        
        checks = [
            ('data-workspace-tab="report"', "综合报告 Tab"),
            ('id="workspaceReport"', "Report 工作台视图"),
            ('class="report-workbench-layout"', "工作台布局"),
            ('class="report-history-sidebar"', "历史侧边栏"),
            ('class="report-main-area"', "主区域"),
            ('class="report-toolbar"', "工具栏"),
            ('id="reportFindingsContent"', "影像所见容器"),
            ('id="reportDiagnosisContent"', "影像诊断容器"),
            ('class="report-copy-btn"', "复制按钮"),
            ('id="reportTextInput"', "文本输入框"),
            ('id="reportUploadBtn"', "上传按钮"),
            ('id="reportLanguageSelect"', "语言选择"),
            ('id="reportSendBtn"', "发送按钮"),
            ('id="reportSaveBtn"', "保存按钮"),
            ('id="reportExportBtn"', "导出按钮"),
        ]
        
        all_passed = True
        for check_str, name in checks:
            exists = check_str in html
            print_test(name, exists)
            all_passed = all_passed and exists
        
        return all_passed
        
    except Exception as e:
        print_test("HTML 加载", False, str(e))
        return False

def main():
    print(f"\n{'='*60}")
    print(f"{COLORS['blue']}Report 工作台功能快速测试{COLORS['end']}")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"目标: {BASE_URL}")
    print('='*60)
    
    results = {}
    
    # 运行测试
    results['API路由'] = test_api_routes()
    results['前端资源'] = test_frontend()
    results['HTML结构'] = test_html_structure()
    results['报告生成'] = test_report_generation()
    
    # 汇总结果
    print_header("测试结果汇总")
    
    total = len(results)
    passed = sum(1 for v in results.values() if v)
    
    for name, result in results.items():
        status = f"{COLORS['green']}✅{COLORS['end']}" if result else f"{COLORS['red']}❌{COLORS['end']}"
        print(f"{status} {name}")
    
    print(f"\n{COLORS['blue']}总计: {passed}/{total} 通过{COLORS['end']}")
    
    if passed == total:
        print(f"\n{COLORS['green']}🎉 所有测试通过！系统功能正常。{COLORS['end']}\n")
        return 0
    else:
        print(f"\n{COLORS['yellow']}⚠️  部分测试失败，请检查相关功能。{COLORS['end']}\n")
        return 1

if __name__ == "__main__":
    exit(main())
