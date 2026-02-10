import subprocess
import re
import time
from collections import Counter
import sys
import os
import psutil

TARGET_DIR = r"."
INPUT_FILENAME = "contracts.txt"
LOG_FILENAME = "errors_details.log"
TIMEOUT_SECONDS = 30


def kill_process_tree(pid):
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        for child in children:
            try:
                child.kill()
            except:
                pass
        parent.kill()
    except:
        pass


def extract_error_message(full_output):
    lines = full_output.splitlines()
    for line in reversed(lines):
        clean = re.sub(r'\x1b\[[0-9;]*m', '', line).strip()
        if "java.lang.IllegalStateException:" in clean:
            return clean.split("java.lang.IllegalStateException:", 1)[1].strip()
        if "java.lang.IllegalArgumentException:" in clean:
            return clean.split("java.lang.IllegalArgumentException:", 1)[1].strip()
        if "Exception:" in clean or "Error:" in clean:
            return clean

    for line in reversed(lines):
        clean = re.sub(r'\x1b\[[0-9;]*m', '', line).strip()
        if clean and not clean.startswith("> Task") and "BUILD FAILED" not in clean:
            return f"Unknown error (last line): {clean}"
    return "Unknown error"


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    jar_path = r"tvm-disasm-cli\build\libs\tvm-disasm-cli.jar"

    input_path = INPUT_FILENAME
    log_path = os.path.join(TARGET_DIR, LOG_FILENAME)

    with open(input_path, 'r', encoding='utf-8') as f:
        addresses = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    stats = Counter()
    total = len(addresses)
    timing_data = []
    timeout_count = 0

    with open(log_path, 'w', encoding='utf-8') as f:
        f.write("=== ERROR LOGS ===\n")

    for i, address in enumerate(addresses, 1):
        command = f'java -jar "{jar_path}" tac --address {address}'

        print(f"[{i}/{total}]:")
        print(f"  Address: {address}")

        start_time = time.time()
        timed_out = False
        process = None

        try:
            process = subprocess.Popen(
                command,
                cwd=TARGET_DIR,
                shell=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding='utf-8',
                errors='replace'
            )

            try:
                stdout, stderr = process.communicate(timeout=TIMEOUT_SECONDS)
                output_combined = stdout + stderr
                execution_time = (time.time() - start_time) * 1000

                if process.returncode == 0:
                    status = "SUCCESS"
                    error_msg = ""
                    print(f"  Result: OK ({execution_time:.1f} ms)")
                    stats["SUCCESS"] += 1
                else:
                    status = "FAIL"
                    error_msg = extract_error_message(output_combined)

                    generic_error = re.sub(r'var_\d+', 'var_XXX', error_msg)
                    generic_error = re.sub(r'arg\d+', 'argXXX', generic_error)
                    generic_error = re.sub(r't_\d+', 't_XXX', generic_error)
                    generic_error = re.sub(r'const_\d+', 'const_XXX', generic_error)

                    print(f"  Result: FAIL ({execution_time:.1f} ms)")
                    print(f"  Error: {generic_error}")
                    stats[generic_error] += 1

                    with open(log_path, 'a', encoding='utf-8') as log:
                        log.write(f"\n--- ADDRESS: {address} ---\n")
                        log.write(f"ERROR: {generic_error}\n")
                        log.write(f"Execution time: {execution_time:.1f} ms\n")
                        log.write(output_combined)
                        log.write("\n" + "=" * 40 + "\n")

            except subprocess.TimeoutExpired:
                timed_out = True
                execution_time = (time.time() - start_time) * 1000

                print(f"  Result: TIMEOUT (превышено {TIMEOUT_SECONDS} секунд)")
                print(f"  Execution time: {execution_time:.1f} ms")

                if process and process.poll() is None:
                    kill_process_tree(process.pid)
                    try:
                        process.wait(timeout=5)
                    except:
                        pass

                status = "TIMEOUT"
                error_msg = f"Timeout after {TIMEOUT_SECONDS} seconds"
                stats["TIMEOUT"] += 1
                timeout_count += 1

                with open(log_path, 'a', encoding='utf-8') as log:
                    log.write(f"\n--- ADDRESS: {address} ---\n")
                    log.write(f"ERROR: {error_msg}\n")
                    log.write(f"Execution time: {execution_time:.1f} ms (превышен лимит {TIMEOUT_SECONDS} секунд)\n")
                    log.write("Process was terminated due to timeout\n")
                    log.write("\n" + "=" * 40 + "\n")

        except Exception as e:
            execution_time = (time.time() - start_time) * 1000
            print(f"SCRIPT CRASH: {e}")
            status = "CRASH"
            error_msg = f"Script Error: {str(e)}"
            stats["SCRIPT_CRASH"] += 1

            if process and process.poll() is None:
                kill_process_tree(process.pid)

        finally:
            if not timed_out and 'execution_time' in locals():
                timing_data.append((address, execution_time, status))

    print("ИТОГОВАЯ СТАТИСТИКА")

    print(f"\nВсего контрактов: {total}")

    if timeout_count > 0:
        print(f"\nПроцессов завершено по таймауту: {timeout_count}")
        print(f"Таймаут установлен на {TIMEOUT_SECONDS} секунд")

    print("\nРезультаты:")
    for error, count in stats.most_common():
        percentage = (count / total) * 100
        print(f"{count:4d} ({percentage:5.1f}%) | {error}")

    if timing_data:
        success_times = [t for _, t, s in timing_data if s == "SUCCESS"]
        fail_times = [t for _, t, s in timing_data if s == "FAIL"]

        print(f"\nВремя выполнения (успешные):")
        if success_times:
            print(f"  Всего успешных: {len(success_times)}")
            print(f"  Среднее время: {sum(success_times) / len(success_times):.1f} ms")
            print(f"  Мин. время: {min(success_times):.1f} ms")
            print(f"  Макс. время: {max(success_times):.1f} ms")
            if len(success_times) > 0:
                print(f"  Медиана: {sorted(success_times)[len(success_times) // 2]:.1f} ms")

        if fail_times:
            print(f"\nВремя выполнения (ошибки):")
            print(f"  Всего с ошибками: {len(fail_times)}")
            print(f"  Среднее время: {sum(fail_times) / len(fail_times):.1f} ms")

    print(f"Детальные логи ошибок в: {log_path}")



if __name__ == "__main__":
    main()