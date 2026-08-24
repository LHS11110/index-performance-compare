"""
Heap Table Operation Benchmark: Non-Clustered Index vs Pure Heap
================================================================
Compares ORDER BY, GROUP BY, JOIN performance across three scenarios:
  1. NC Index covers the target column  (index hit)
  2. NC Index exists but on a different column  (index miss)
  3. Pure Heap — no indexes at all

Data sizes: 100, 1000, 10000, 100000
All tables are created as heap tables (no clustered index).
Tables are cleaned up after the benchmark.
"""

import pyodbc
import time
import random
import json
import os
import sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
from tabulate import tabulate


# ── Configuration ─────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, 'db_config.json')
with open(CONFIG_PATH, 'r') as f:
    _db_config = json.load(f)

DRIVER = _db_config.get('DRIVER', '{ODBC Driver 18 for SQL Server}')
SERVER = _db_config.get('SERVER', 'localhost')
UID = _db_config['UID']
PWD = _db_config['PWD']
DATABASE = _db_config['DATABASE']

DATA_SIZES = [100, 1000, 10000, 100000]
OUTPUT_DIR = os.path.join(BASE_DIR, 'result')
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Number of repeated measurements for averaging
REPEAT_COUNT = 100

# Filter divisor: queries process N/FILTER_DIVISOR rows via inline view with TOP
FILTER_DIVISOR = 10
FILTER_PCT = 100 / FILTER_DIVISOR  # percentage of data used

# ── Three scenarios ───────────────────────────────────────────────────
# The "target column" for ORDER BY / GROUP BY / JOIN will be val2 (INT).
# Scenario 1: NC index on val2  → index covers the target column
# Scenario 2: NC index on val1  → index exists but on a different column
# Scenario 3: No index at all   → pure heap
SCENARIOS = [
    'NC Index (covers column)',
    'NC Index (other column)',
    'Pure Heap (no index)',
]


# ── Helpers ───────────────────────────────────────────────────────────
def get_connection(database=None, autocommit=False):
    cs = (
        f"DRIVER={DRIVER};SERVER={SERVER};UID={UID};PWD={PWD};"
        f"TrustServerCertificate=yes;"
    )
    if database:
        cs += f"DATABASE={database};"
    conn = pyodbc.connect(cs, autocommit=autocommit)
    return conn


def ensure_database():
    conn = get_connection(autocommit=True)
    cursor = conn.cursor()
    cursor.execute(f"""
        IF NOT EXISTS (SELECT 1 FROM sys.databases WHERE name = '{DATABASE}')
            CREATE DATABASE [{DATABASE}];
    """)
    conn.close()


def drop_table(cursor, table):
    cursor.execute(f"IF OBJECT_ID('{table}', 'U') IS NOT NULL DROP TABLE [{table}];")
    cursor.connection.commit()


def timed(func):
    start = time.perf_counter()
    func()
    return time.perf_counter() - start


def timed_avg(func, repeats=REPEAT_COUNT):
    """Run func multiple times and return the average elapsed time."""
    total = 0.0
    for _ in range(repeats):
        total += timed(func)
    return total / repeats


# ── Data population ──────────────────────────────────────────────────
def populate_table(cursor, table, n, batch_size=1000):
    """Insert n rows into a heap table."""
    sql = f"INSERT INTO [{table}] (id, val1, val2, val3) VALUES (?, ?, ?, ?)"
    for i in range(0, n, batch_size):
        chunk_end = min(i + batch_size, n)
        data = []
        for row_id in range(i + 1, chunk_end + 1):
            val2 = random.randint(1, 1_000_000)
            month = random.randint(1, 12)
            day = random.randint(1, 28)
            data.append((row_id, f'data_{row_id}', val2, f'2024-{month:02d}-{day:02d}'))
        cursor.executemany(sql, data)
        cursor.connection.commit()


def create_heap_table(cursor, table):
    """Create a pure heap table (no PK, no indexes)."""
    cursor.execute(f"""
        CREATE TABLE [{table}] (
            id   INT          NOT NULL,
            val1 NVARCHAR(100),
            val2 INT,
            val3 DATETIME
        );
    """)
    cursor.connection.commit()


def create_join_table(cursor, table, n, batch_size=1000):
    """Create and populate a second heap table for JOIN tests."""
    cursor.execute(f"""
        CREATE TABLE [{table}] (
            ref_id   INT          NOT NULL,
            detail   NVARCHAR(100),
            amount   INT
        );
    """)
    cursor.connection.commit()

    sql = f"INSERT INTO [{table}] (ref_id, detail, amount) VALUES (?, ?, ?)"
    # Create rows that reference some of the main table's val2 values.
    # We pick random ref_id values in the same range as val2.
    for i in range(0, n, batch_size):
        chunk_end = min(i + batch_size, n)
        data = []
        for _ in range(chunk_end - i):
            ref_id = random.randint(1, 1_000_000)
            data.append((ref_id, f'detail_{ref_id}', random.randint(1, 10000)))
        cursor.executemany(sql, data)
        cursor.connection.commit()


# ── Benchmark functions ──────────────────────────────────────────────
def bench_order_by(cursor, table, n):
    """ORDER BY val2 — inline view with TOP to limit to N/FILTER_DIVISOR rows."""
    top_n = max(1, n // FILTER_DIVISOR)
    sql = f"""
        SELECT sub.id, sub.val1, sub.val2, sub.val3
        FROM (SELECT TOP ({top_n}) id, val1, val2, val3 FROM [{table}]) sub
        ORDER BY sub.val2
    """

    def do():
        cursor.execute("DBCC DROPCLEANBUFFERS")
        cursor.execute(sql)
        cursor.fetchall()
    return timed_avg(do)


def bench_group_by(cursor, table, n):
    """GROUP BY on val2 — inline view with TOP to limit to N/FILTER_DIVISOR rows."""
    top_n = max(1, n // FILTER_DIVISOR)
    sql = f"""
        SELECT sub.val2, COUNT(*) AS cnt, AVG(sub.id) AS avg_id
        FROM (SELECT TOP ({top_n}) id, val1, val2, val3 FROM [{table}]) sub
        GROUP BY sub.val2
    """

    def do():
        cursor.execute("DBCC DROPCLEANBUFFERS")
        cursor.execute(sql)
        cursor.fetchall()
    return timed_avg(do)


def bench_join(cursor, main_table, join_table, n):
    """INNER JOIN — inline views with TOP to limit to N/FILTER_DIVISOR rows."""
    top_n = max(1, n // FILTER_DIVISOR)
    sql = f"""
        SELECT a.id, a.val1, a.val2, b.detail, b.amount
        FROM (SELECT TOP ({top_n}) id, val1, val2, val3 FROM [{main_table}]) a
        INNER JOIN (SELECT TOP ({top_n}) ref_id, detail, amount FROM [{join_table}]) b
            ON a.val2 = b.ref_id
    """

    def do():
        cursor.execute("DBCC DROPCLEANBUFFERS")
        cursor.execute(sql)
        cursor.fetchall()
    return timed_avg(do)


# ── Setup helpers per scenario ───────────────────────────────────────
def setup_scenario(cursor, main_tbl, join_tbl, scenario, n):
    """
    Create tables, populate data, and add indexes as needed.
    Returns (main_tbl, join_tbl) names.
    """
    # Create & populate main table
    drop_table(cursor, main_tbl)
    create_heap_table(cursor, main_tbl)
    populate_table(cursor, main_tbl, n)

    # Create & populate join table
    drop_table(cursor, join_tbl)
    create_join_table(cursor, join_tbl, n)

    if scenario == 'NC Index (covers column)':
        # NC index on val2 — the column used for ORDER BY / GROUP BY / JOIN
        cursor.execute(f"CREATE NONCLUSTERED INDEX IX_{main_tbl}_val2 ON [{main_tbl}](val2);")
        cursor.connection.commit()
        # Also add index on join table's ref_id for JOIN
        cursor.execute(f"CREATE NONCLUSTERED INDEX IX_{join_tbl}_ref ON [{join_tbl}](ref_id);")
        cursor.connection.commit()

    elif scenario == 'NC Index (other column)':
        # NC index on val1 — a DIFFERENT column, not the one used in queries
        cursor.execute(f"CREATE NONCLUSTERED INDEX IX_{main_tbl}_val1 ON [{main_tbl}](val1);")
        cursor.connection.commit()
        # Also add index on join table's detail (not ref_id)
        cursor.execute(f"CREATE NONCLUSTERED INDEX IX_{join_tbl}_detail ON [{join_tbl}](detail);")
        cursor.connection.commit()

    # scenario == 'Pure Heap (no index)' → nothing to add

    return main_tbl, join_tbl


# ── Main benchmark driver ────────────────────────────────────────────
def run_benchmarks():
    ensure_database()

    operations = ['ORDER BY', 'GROUP BY', 'JOIN']

    # results[scenario][operation] = list of times per DATA_SIZE
    results = {s: {op: [] for op in operations} for s in SCENARIOS}

    conn = get_connection(DATABASE, autocommit=False)
    cursor = conn.cursor()
    cursor.fast_executemany = True

    total_tests = len(SCENARIOS) * len(DATA_SIZES)
    test_num = 0

    for scenario in SCENARIOS:
        for size in DATA_SIZES:
            test_num += 1
            top_n = max(1, size // FILTER_DIVISOR)
            print(f"\n{'='*60}", flush=True)
            print(f"[{test_num}/{total_tests}] {scenario} | N = {size:,} | TOP {top_n:,} ({FILTER_PCT:.0f}%)", flush=True)
            print(f"{'='*60}", flush=True)

            safe = scenario.replace(' ', '_').replace('(', '').replace(')', '').replace(',', '')
            main_tbl = f"bh_{safe}_{size}_main"
            join_tbl = f"bh_{safe}_{size}_join"

            setup_scenario(cursor, main_tbl, join_tbl, scenario, size)

            # ── ORDER BY ──────────────────────────────────────────
            t = bench_order_by(cursor, main_tbl, size)
            results[scenario]['ORDER BY'].append(t)
            print(f"  ORDER BY : {t:.6f}s  (avg of {REPEAT_COUNT})", flush=True)

            # ── GROUP BY ──────────────────────────────────────────
            t = bench_group_by(cursor, main_tbl, size)
            results[scenario]['GROUP BY'].append(t)
            print(f"  GROUP BY : {t:.6f}s  (avg of {REPEAT_COUNT})", flush=True)

            # ── JOIN ──────────────────────────────────────────────
            t = bench_join(cursor, main_tbl, join_tbl, size)
            results[scenario]['JOIN'].append(t)
            print(f"  JOIN     : {t:.6f}s  (avg of {REPEAT_COUNT})", flush=True)

            # Cleanup
            drop_table(cursor, main_tbl)
            drop_table(cursor, join_tbl)

    conn.close()
    return results, operations


# ── Visualization ─────────────────────────────────────────────────────
def plot_results(results, operations):
    colors = {
        'NC Index (covers column)': '#2ecc71',
        'NC Index (other column)':  '#f39c12',
        'Pure Heap (no index)':     '#e74c3c',
    }
    markers = {
        'NC Index (covers column)': '^',
        'NC Index (other column)':  's',
        'Pure Heap (no index)':     'o',
    }

    # ── 1. Line charts per operation (1×3 grid) ──────────────────
    fig, axes = plt.subplots(1, 3, figsize=(22, 7))
    fig.suptitle(
        'Heap Table Operation Benchmark: NC Index vs Pure Heap\n'
        f'(ORDER BY / GROUP BY / JOIN) — Using {FILTER_PCT:.0f}% of data (TOP N/{FILTER_DIVISOR})',
        fontsize=16, fontweight='bold', y=1.04,
    )

    for idx, op in enumerate(operations):
        ax = axes[idx]
        for scenario in SCENARIOS:
            times = results[scenario][op]
            ax.plot(
                DATA_SIZES, times,
                marker=markers[scenario],
                label=scenario,
                color=colors[scenario],
                linewidth=2.2, markersize=7,
            )
            for x, y in zip(DATA_SIZES, times):
                ax.annotate(
                    f'{y:.4f}s',
                    (x, y),
                    textcoords='offset points',
                    xytext=(0, 10),
                    fontsize=7,
                    ha='center',
                    fontweight='bold',
                    color=colors[scenario],
                )
        ax.set_title(op, fontsize=14, fontweight='bold')
        ax.set_xlabel('Data Size (rows)')
        ax.set_ylabel('Time (seconds)')
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
        ax.set_xscale('log')
        ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))

    plt.tight_layout(rect=[0, 0, 1, 0.95])
    path1 = os.path.join(OUTPUT_DIR, 'heap_ops_lines.png')
    plt.savefig(path1, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"\nLine chart saved: {path1}")

    # ── 2. Grouped bar charts per data size ──────────────────────
    fig2, axes2 = plt.subplots(1, len(DATA_SIZES), figsize=(24, 6), sharey=False)
    fig2.suptitle(
        f'Heap Operation Performance by Data Size — Using {FILTER_PCT:.0f}% of data (TOP N/{FILTER_DIVISOR})',
        fontsize=16, fontweight='bold', y=1.02,
    )

    bar_width = 0.25

    for size_idx, size in enumerate(DATA_SIZES):
        ax = axes2[size_idx]
        x = np.arange(len(operations))

        for s_idx, scenario in enumerate(SCENARIOS):
            times = [results[scenario][op][size_idx] for op in operations]
            bars = ax.bar(
                x + s_idx * bar_width, times, bar_width,
                label=scenario, color=colors[scenario], alpha=0.85,
            )
            for bar, val in zip(bars, times):
                ax.text(
                    bar.get_x() + bar.get_width() / 2, bar.get_height(),
                    f'{val:.4f}', ha='center', va='bottom', fontsize=6,
                    fontweight='bold',
                )

        ax.set_title(f'N = {size:,}', fontsize=12, fontweight='bold')
        ax.set_xticks(x + bar_width)
        ax.set_xticklabels(operations, fontsize=9)
        ax.set_ylabel('Time (s)')
        if size_idx == 0:
            ax.legend(fontsize=6, loc='upper left')
        ax.grid(axis='y', alpha=0.3)

    plt.tight_layout()
    path2 = os.path.join(OUTPUT_DIR, 'heap_ops_bars.png')
    plt.savefig(path2, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Bar chart saved: {path2}")

    # ── 3. Heatmap comparison ─────────────────────────────────────
    fig3, axes3 = plt.subplots(1, 3, figsize=(22, 5))
    fig3.suptitle(
        f'Heatmap: Heap Operation Execution Time (seconds) — Using {FILTER_PCT:.0f}% of data (TOP N/{FILTER_DIVISOR})',
        fontsize=16, fontweight='bold', y=1.04,
    )

    for s_idx, scenario in enumerate(SCENARIOS):
        ax = axes3[s_idx]
        data = np.array([results[scenario][op] for op in operations])
        im = ax.imshow(data, cmap='YlOrRd', aspect='auto')

        ax.set_xticks(range(len(DATA_SIZES)))
        ax.set_xticklabels([f'{s:,}' for s in DATA_SIZES], fontsize=9)
        ax.set_yticks(range(len(operations)))
        ax.set_yticklabels(operations, fontsize=10)
        ax.set_title(scenario, fontsize=12, fontweight='bold',
                      color=colors[scenario])
        ax.set_xlabel('Data Size')

        for i in range(len(operations)):
            for j in range(len(DATA_SIZES)):
                val = data[i, j]
                ax.text(j, i, f'{val:.4f}s', ha='center', va='center',
                        fontsize=8, fontweight='bold',
                        color='white' if val > data.max() * 0.6 else 'black')

        plt.colorbar(im, ax=ax, shrink=0.8)

    plt.tight_layout()
    path3 = os.path.join(OUTPUT_DIR, 'heap_ops_heatmap.png')
    plt.savefig(path3, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Heatmap saved: {path3}")

    return path1, path2, path3


def print_summary_tables(results, operations):
    for op in operations:
        print(f"\n{'─'*70}")
        print(f"  {op}")
        print(f"{'─'*70}")
        headers = ['Scenario'] + [f'N={s:,}' for s in DATA_SIZES]
        rows = []
        for scenario in SCENARIOS:
            row = [scenario] + [
                f'{t:.6f}s' for t in results[scenario][op]
            ]
            rows.append(row)
        print(tabulate(rows, headers=headers, tablefmt='grid'))


# ── Entry point ───────────────────────────────────────────────────────
if __name__ == '__main__':
    print("=" * 60)
    print("  Heap Table Operation Benchmark")
    print("  NC Index vs Pure Heap: ORDER BY / GROUP BY / JOIN")
    print("=" * 60)
    print(f"  Data sizes    : {DATA_SIZES}")
    print(f"  Scenarios     : {SCENARIOS}")
    print(f"  Repeat count  : {REPEAT_COUNT}")
    print(f"  Filter divisor: 1/{FILTER_DIVISOR} (TOP N/{FILTER_DIVISOR} = {FILTER_PCT:.0f}% of data)")
    print()

    results, operations = run_benchmarks()

    print("\n\n" + "=" * 60)
    print("  BENCHMARK RESULTS SUMMARY")
    print("=" * 60)
    print_summary_tables(results, operations)

    charts = plot_results(results, operations)

    # Save raw results to JSON
    json_path = os.path.join(OUTPUT_DIR, 'heap_ops_raw.json')
    json_data = {
        'data_sizes': DATA_SIZES,
        'repeat_count': REPEAT_COUNT,
        'scenarios': SCENARIOS,
        'results': {
            s: {op: times for op, times in ops.items()}
            for s, ops in results.items()
        },
    }
    with open(json_path, 'w') as f:
        json.dump(json_data, f, indent=2)
    print(f"\nRaw JSON saved: {json_path}")
    print("\n✅ Heap operation benchmark complete!")
