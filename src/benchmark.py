"""
SQL Server Storage Structure Benchmark (Optimized)
====================================================
Compares performance of:
  1. Pure Heap (no indexes)
  2. Heap + Non-Clustered Index (on PK column)
  3. Clustered Index (on PK column)

Measures: Sequential Insert, Random Insert, Select (point),
          Select (range), Update, Delete

Data sizes: 1000, 5000, 10000, 50000, 100000
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
OUTPUT_DIR = os.path.join(BASE_DIR, 'result', 'basic')
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Number of individual operations for point queries / updates / deletes
SAMPLE_COUNT = 100

STRUCTURES = {
    'Pure Heap': {
        'create': """
            CREATE TABLE {table} (
                id INT NOT NULL,
                val1 NVARCHAR(100),
                val2 INT,
                val3 DATETIME
            );
        """,
    },
    'Heap + NC Index': {
        'create': """
            CREATE TABLE {table} (
                id INT NOT NULL PRIMARY KEY NONCLUSTERED,
                val1 NVARCHAR(100),
                val2 INT,
                val3 DATETIME
            );
        """,
    },
    'Clustered Index': {
        'create': """
            CREATE TABLE {table} (
                id INT NOT NULL PRIMARY KEY CLUSTERED,
                val1 NVARCHAR(100),
                val2 INT,
                val3 DATETIME
            );
        """,
    },
}


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


# ── Batch insert using VALUES list (much faster than executemany) ─────
def bulk_insert_batch(cursor, table, ids, batch_size=1000):
    """Insert rows using executemany for speed."""
    sql = f"INSERT INTO [{table}] (id, val1, val2, val3) VALUES (?, ?, ?, ?)"
    for i in range(0, len(ids), batch_size):
        chunk = ids[i:i + batch_size]
        data = []
        for row_id in chunk:
            val1 = f'data_{row_id}'
            val2 = random.randint(1, 1_000_000)
            month = random.randint(1, 12)
            day = random.randint(1, 28)
            data.append((row_id, f'data_{row_id}', val2, f'2024-{month:02d}-{day:02d}'))
        cursor.executemany(sql, data)
        cursor.connection.commit()


# ── Benchmark functions ──────────────────────────────────────────────
def bench_sequential_insert(cursor, table, n):
    """Insert n rows sequentially (id = 1..n) using batch INSERT."""
    ids = list(range(1, n + 1))
    def do():
        bulk_insert_batch(cursor, table, ids)
    return timed(do)


def bench_random_insert(cursor, table, n):
    """Insert n rows in random id order using batch INSERT."""
    ids = list(range(1, n + 1))
    random.shuffle(ids)
    def do():
        bulk_insert_batch(cursor, table, ids)
    return timed(do)


def bench_select_point(cursor, table, n, sample_count=SAMPLE_COUNT):
    """Point-select random rows by PK (id)."""
    ids = random.sample(range(1, n + 1), min(sample_count, n))
    sql = f"SELECT * FROM [{table}] WHERE id = ?"

    def do():
        for pk in ids:
            cursor.execute(sql, pk)
            cursor.fetchall()
    return timed(do) / len(ids)


def bench_select_range(cursor, table, n, range_pct=10, sample_count=SAMPLE_COUNT):
    """Range scan by PK: SELECT WHERE id BETWEEN ... (10% of data)."""
    range_size = max(1, int(n * range_pct / 100))
    sql = f"SELECT * FROM [{table}] WHERE id BETWEEN ? AND ?"

    def do():
        for _ in range(sample_count):
            start_id = random.randint(1, max(1, n - range_size + 1))
            end_id = start_id + range_size - 1
            cursor.execute(sql, start_id, end_id)
            cursor.fetchall()
    return timed(do) / sample_count


def bench_update(cursor, table, n, sample_count=SAMPLE_COUNT):
    """Update random rows by PK."""
    ids = random.sample(range(1, n + 1), min(sample_count, n))
    sql = f"UPDATE [{table}] SET val1 = ?, val2 = ? WHERE id = ?"
    data = [(f'updated_{pk}', random.randint(1, 999999), pk) for pk in ids]

    def do():
        cursor.executemany(sql, data)
        cursor.connection.commit()
    return timed(do)


def bench_delete(cursor, table, n, sample_count=SAMPLE_COUNT):
    """Delete random rows by PK."""
    ids = random.sample(range(1, n + 1), min(sample_count, n))
    sql = f"DELETE FROM [{table}] WHERE id = ?"
    data = [(pk,) for pk in ids]

    def do():
        cursor.executemany(sql, data)
        cursor.connection.commit()
    return timed(do)


# ── Main benchmark driver ────────────────────────────────────────────
def run_benchmarks():
    ensure_database()

    operations = [
        'Sequential Insert',
        'Random Insert',
        'Select (Point)',
        'Select (Range)',
        'Update',
        'Delete',
    ]

    results = {s: {op: [] for op in operations} for s in STRUCTURES}

    conn = get_connection(DATABASE, autocommit=False)
    cursor = conn.cursor()
    cursor.fast_executemany = True

    total_tests = len(STRUCTURES) * len(DATA_SIZES)
    test_num = 0

    for struct_name, struct_cfg in STRUCTURES.items():
        for size in DATA_SIZES:
            test_num += 1
            print(f"\n{'='*60}", flush=True)
            print(f"[{test_num}/{total_tests}] {struct_name} | N = {size:,}", flush=True)
            print(f"{'='*60}", flush=True)

            safe_name = struct_name.replace(' ', '_').replace('+', 'P')

            # ── Sequential Insert ─────────────────────────────────
            tbl = f"b_{safe_name}_{size}_seq"
            drop_table(cursor, tbl)
            cursor.execute(struct_cfg['create'].format(table=tbl))
            cursor.connection.commit()
            t = bench_sequential_insert(cursor, tbl, size)
            results[struct_name]['Sequential Insert'].append(t)
            print(f"  Sequential Insert : {t:.6f}s", flush=True)
            drop_table(cursor, tbl)

            # ── Random Insert ─────────────────────────────────────
            tbl = f"b_{safe_name}_{size}_rnd"
            drop_table(cursor, tbl)
            cursor.execute(struct_cfg['create'].format(table=tbl))
            cursor.connection.commit()
            t = bench_random_insert(cursor, tbl, size)
            results[struct_name]['Random Insert'].append(t)
            print(f"  Random Insert     : {t:.6f}s", flush=True)
            drop_table(cursor, tbl)

            # ── Prepare table for Read / Update / Delete tests ────
            tbl = f"b_{safe_name}_{size}_crud"
            drop_table(cursor, tbl)
            cursor.execute(struct_cfg['create'].format(table=tbl))
            cursor.connection.commit()
            # Populate with sequential data
            ids = list(range(1, size + 1))
            bulk_insert_batch(cursor, tbl, ids)

            # Clear buffer pool to get fair read measurements
            cursor.execute("DBCC DROPCLEANBUFFERS")

            # ── Select (Point) ────────────────────────────────────
            t = bench_select_point(cursor, tbl, size)
            results[struct_name]['Select (Point)'].append(t)
            print(f"  Select (Point)    : {t:.6f}s", flush=True)

            # ── Select (Range) ────────────────────────────────────
            cursor.execute("DBCC DROPCLEANBUFFERS")
            t = bench_select_range(cursor, tbl, size)
            results[struct_name]['Select (Range)'].append(t)
            print(f"  Select (Range)    : {t:.6f}s", flush=True)

            # ── Update ────────────────────────────────────────────
            t = bench_update(cursor, tbl, size)
            results[struct_name]['Update'].append(t)
            print(f"  Update            : {t:.6f}s", flush=True)

            # ── Delete ────────────────────────────────────────────
            t = bench_delete(cursor, tbl, size)
            results[struct_name]['Delete'].append(t)
            print(f"  Delete            : {t:.6f}s", flush=True)

            drop_table(cursor, tbl)

    conn.close()
    return results, operations


# ── Visualization ─────────────────────────────────────────────────────
def plot_results(results, operations):
    colors = {
        'Pure Heap':        '#e74c3c',
        'Heap + NC Index':  '#3498db',
        'Clustered Index':  '#2ecc71',
    }
    markers = {
        'Pure Heap':        'o',
        'Heap + NC Index':  's',
        'Clustered Index':  '^',
    }

    # ── 1. Line charts per operation (2×3 grid) ──────────────────
    fig, axes = plt.subplots(2, 3, figsize=(20, 12))
    fig.suptitle(
        'SQL Server Storage Structure Performance Benchmark',
        fontsize=18, fontweight='bold', y=0.98,
    )

    for idx, op in enumerate(operations):
        ax = axes[idx // 3][idx % 3]
        for struct_name in STRUCTURES:
            times = results[struct_name][op]
            ax.plot(
                DATA_SIZES, times,
                marker=markers[struct_name],
                label=struct_name,
                color=colors[struct_name],
                linewidth=2.2, markersize=7,
            )
            for x, y in zip(DATA_SIZES, times):
                ax.annotate(
                    f'{y:.6f}s',
                    (x, y),
                    textcoords='offset points',
                    xytext=(0, 10),
                    fontsize=7,
                    ha='center',
                    fontweight='bold',
                    color=colors[struct_name],
                )
        ax.set_title(op, fontsize=13, fontweight='bold')
        ax.set_xlabel('Data Size (rows)')
        ax.set_ylabel('Time (seconds)')
        ax.legend(fontsize=9)
        ax.grid(True, alpha=0.3)
        ax.set_xscale('log')
        ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))

    plt.tight_layout(rect=[0, 0, 1, 0.95])
    path1 = os.path.join(OUTPUT_DIR, 'benchmark_lines.png')
    plt.savefig(path1, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"\nLine chart saved: {path1}")

    # ── 2. Grouped bar chart per data size ────────────────────────
    fig2, axes2 = plt.subplots(1, len(DATA_SIZES), figsize=(26, 6),
                               sharey=False)
    fig2.suptitle(
        'Performance Comparison by Data Size',
        fontsize=16, fontweight='bold', y=1.02,
    )

    bar_width = 0.25
    struct_names = list(STRUCTURES.keys())

    for size_idx, size in enumerate(DATA_SIZES):
        ax = axes2[size_idx]
        x = np.arange(len(operations))

        for s_idx, struct_name in enumerate(struct_names):
            times = [results[struct_name][op][size_idx] for op in operations]
            bars = ax.bar(
                x + s_idx * bar_width, times, bar_width,
                label=struct_name, color=colors[struct_name], alpha=0.85,
            )
            # Put value labels on bars
            for bar, val in zip(bars, times):
                ax.text(
                    bar.get_x() + bar.get_width() / 2, bar.get_height(),
                    f'{val:.3f}', ha='center', va='bottom', fontsize=5,
                    fontweight='bold',
                )

        ax.set_title(f'N = {size:,}', fontsize=12, fontweight='bold')
        ax.set_xticks(x + bar_width)
        ax.set_xticklabels([op.replace(' ', '\n') for op in operations],
                           fontsize=6.5)
        ax.set_ylabel('Time (s)')
        if size_idx == 0:
            ax.legend(fontsize=7, loc='upper left')
        ax.grid(axis='y', alpha=0.3)

    plt.tight_layout()
    path2 = os.path.join(OUTPUT_DIR, 'benchmark_bars.png')
    plt.savefig(path2, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Bar chart saved: {path2}")

    # ── 3. Heatmap comparison ─────────────────────────────────────
    fig3, axes3 = plt.subplots(1, 3, figsize=(22, 7))
    fig3.suptitle(
        'Heatmap: Execution Time (seconds)',
        fontsize=16, fontweight='bold', y=1.02,
    )

    for s_idx, struct_name in enumerate(struct_names):
        ax = axes3[s_idx]
        data = np.array([results[struct_name][op] for op in operations])
        im = ax.imshow(data, cmap='YlOrRd', aspect='auto')

        ax.set_xticks(range(len(DATA_SIZES)))
        ax.set_xticklabels([f'{s:,}' for s in DATA_SIZES], fontsize=9)
        ax.set_yticks(range(len(operations)))
        ax.set_yticklabels(operations, fontsize=9)
        ax.set_title(struct_name, fontsize=13, fontweight='bold',
                      color=colors[struct_name])
        ax.set_xlabel('Data Size')

        # Annotate cells
        for i in range(len(operations)):
            for j in range(len(DATA_SIZES)):
                val = data[i, j]
                ax.text(j, i, f'{val:.6f}s', ha='center', va='center',
                        fontsize=7, fontweight='bold',
                        color='white' if val > data.max() * 0.6 else 'black')

        plt.colorbar(im, ax=ax, shrink=0.8)

    plt.tight_layout()
    path3 = os.path.join(OUTPUT_DIR, 'benchmark_heatmap.png')
    plt.savefig(path3, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Heatmap saved: {path3}")

    return path1, path2, path3


def print_summary_tables(results, operations):
    for op in operations:
        print(f"\n{'─'*70}")
        print(f"  {op}")
        print(f"{'─'*70}")
        headers = ['Structure'] + [f'N={s:,}' for s in DATA_SIZES]
        rows = []
        for struct_name in STRUCTURES:
            row = [struct_name] + [
                f'{t:.6f}s' for t in results[struct_name][op]
            ]
            rows.append(row)
        print(tabulate(rows, headers=headers, tablefmt='grid'))


# ── Entry point ───────────────────────────────────────────────────────
if __name__ == '__main__':
    print("=" * 60)
    print("  SQL Server Storage Structure Benchmark")
    print("=" * 60)
    print(f"  Data sizes  : {DATA_SIZES}")
    print(f"  Structures  : {list(STRUCTURES.keys())}")
    print(f"  Sample count: {SAMPLE_COUNT} (for point queries, updates, deletes)")
    print()

    results, operations = run_benchmarks()

    print("\n\n" + "=" * 60)
    print("  BENCHMARK RESULTS SUMMARY")
    print("=" * 60)
    print_summary_tables(results, operations)

    charts = plot_results(results, operations)

    # Save raw results to JSON
    json_path = os.path.join(OUTPUT_DIR, 'benchmark_raw.json')
    json_data = {
        'data_sizes': DATA_SIZES,
        'sample_count': SAMPLE_COUNT,
        'results': {
            s: {op: times for op, times in ops.items()}
            for s, ops in results.items()
        },
    }
    with open(json_path, 'w') as f:
        json.dump(json_data, f, indent=2)
    print(f"\nRaw JSON saved: {json_path}")
    print("\n✅ Benchmark complete!")
