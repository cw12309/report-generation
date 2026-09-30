"""
Report 工作台自动化测试脚本
测试关键功能是否正常工作
"""

import requests
import json
import time
import sys
from datetime import datetime

# 设置UTF-8编码
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

BASE_URL = "http://localhost:8000"
CONVERSATION_ID = f"test_{int(time.time())}"

print("=" * 60)
print("Report 工作台功能测试")
print("=" * 60)
print(f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"测试会话ID: {CONVERSATION_ID}")
print("=" * 60)

def test_routes_api():
    """测试1: 路由列表API"""
    print("\n[测试1] 路由列表API")
    try:
        response = requests.get(f"{BASE_URL}/api/report-workbench/routes", timeout=5)
        if response.status_code == 200:
            data = response.json()
            routes = data.get("routes", [])
            print(f"  ✓ 成功获取 {len(routes)} 个路由")
            for route in routes:
                print(f"    - {route['id']}: {route['name']}")
            return True
        else:
            print(f"  ✗ HTTP {response.status_code}")
            return False
    except Exception as e:
        print(f"  ✗ 错误: {e}")
        return False

def test_generate_api():
    """测试2: 报告生成API（腰椎MRI）"""
    print("\n[测试2] 报告生成API")
    try:
        input_text = "腰4/5椎间盘向后中央突出，硬膜囊受压，椎管狭窄"
        print(f"  输入: {input_text}")
        
        params = {
            "input_text": input_text,
            "language": "zh-CN",
            "modality": "",
            "body_part": "",
            "route_id": ""
        }
        
        url = f"{BASE_URL}/api/report-workbench/sessions/{CONVERSATION_ID}/generate"
        print(f"  正在连接SSE... {url}")
        
        # 使用stream模式接收SSE
        response = requests.get(url, params=params, stream=True, timeout=30)
        
        if response.status_code != 200:
            print(f"  ✗ HTTP {response.status_code}")
            return False
        
        events = []
        route_name = None
        report_generated = False
        
        print("  接收SSE事件:")
        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue
            
            if line.startswith("data: "):
                data_str = line[6:]
                if data_str == "[DONE]":
                    continue
                
                try:
                    data = json.loads(data_str)
                    event_type = data.get("type", "unknown")
                    events.append(event_type)
                    
                    if event_type == "conversation":
                        print(f"    - conversation_id: {data.get('conversation_id')}")
                    elif event_type == "route_resolved":
                        route_name = data.get("route_name")
                        print(f"    - route_resolved: {route_name}")
                    elif event_type == "report_updated":
                        session = data.get("session", {})
                        findings = session.get("report", {}).get("imaging_findings", "")
                        diagnosis = session.get("report", {}).get("imaging_diagnosis", "")
                        print(f"    - report_updated: 影像所见{len(findings)}字, 影像诊断{len(diagnosis)}字")
                        if findings and diagnosis:
                            report_generated = True
                    elif event_type == "report_questions":
                        questions = data.get("questions", [])
                        print(f"    - report_questions: {len(questions)}个追问")
                    elif event_type == "done":
                        print(f"    - done: 完成")
                        break
                    elif event_type == "error":
                        print(f"    ✗ error: {data.get('message')}")
                        return False
                        
                except json.JSONDecodeError:
                    continue
        
        print(f"\n  事件序列: {' → '.join(events)}")
        
        if route_name == "腰椎 MRI" and report_generated:
            print(f"  ✓ 报告生成成功")
            return True
        else:
            print(f"  ✗ 报告生成不完整")
            return False
            
    except Exception as e:
        print(f"  ✗ 错误: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_session_get():
    """测试3: 获取会话API"""
    print("\n[测试3] 获取会话API")
    try:
        response = requests.get(
            f"{BASE_URL}/api/report-workbench/sessions/{CONVERSATION_ID}",
            timeout=5
        )
        if response.status_code == 200:
            data = response.json()
            report = data.get("report", {})
            findings_len = len(report.get("imaging_findings", ""))
            diagnosis_len = len(report.get("imaging_diagnosis", ""))
            print(f"  ✓ 会话存在")
            print(f"    - 影像所见: {findings_len}字")
            print(f"    - 影像诊断: {diagnosis_len}字")
            print(f"    - 追问轮次: {data.get('question_round')}/2")
            print(f"    - 版本: v{data.get('current_version')}")
            return True
        elif response.status_code == 404:
            print(f"  ⚠ 会话不存在（可能生成失败）")
            return False
        else:
            print(f"  ✗ HTTP {response.status_code}")
            return False
    except Exception as e:
        print(f"  ✗ 错误: {e}")
        return False

def test_health():
    """测试0: 服务健康检查"""
    print("\n[测试0] 服务健康检查")
    try:
        response = requests.get(f"{BASE_URL}/", timeout=5)
        if response.status_code == 200:
            print(f"  ✓ 服务运行正常")
            return True
        else:
            print(f"  ✗ HTTP {response.status_code}")
            return False
    except Exception as e:
        print(f"  ✗ 服务无法访问: {e}")
        return False

# 执行测试
results = []

results.append(("服务健康检查", test_health()))
time.sleep(0.5)

results.append(("路由列表API", test_routes_api()))
time.sleep(0.5)

results.append(("报告生成API", test_generate_api()))
time.sleep(1)

results.append(("获取会话API", test_session_get()))

# 输出测试结果
print("\n" + "=" * 60)
print("测试结果汇总")
print("=" * 60)

passed = 0
failed = 0

for name, result in results:
    status = "✓ 通过" if result else "✗ 失败"
    print(f"{name:20s} {status}")
    if result:
        passed += 1
    else:
        failed += 1

print("=" * 60)
print(f"总计: {passed + failed} 个测试")
print(f"通过: {passed} 个 ({passed/(passed+failed)*100:.1f}%)")
print(f"失败: {failed} 个")
print("=" * 60)

if failed == 0:
    print("\n🎉 所有测试通过！Report工作台功能正常。")
else:
    print(f"\n⚠️  有 {failed} 个测试失败，请检查日志。")
