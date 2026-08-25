"""
CUD Benchmark: Index Usage Comparison
=====================================
Compares CUD (Insert, Update, Delete) performance when the condition
uses an index vs does not use an index.

Scenarios:
  1. Pure Heap
  2. Clustered Index (on PK)
  3. NC Index (on PK)
  4. NC Index (on non-PK column 'val2')
  5. CI on PK + NC on 'val2'

Data sizes: 100, 1000, 10000, 100000
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
OUTPUT_DIR = os.path.join(BASE_DIR, 'result', 'cud')
os.makedirs(OUTPUT_DIR, exist_ok=True)

SAMPLE_COUNT = 100

STRUCTURES = {
    'Pure Heap': {
        'create': """
            CREATE TABLE [{table}] (
                id INT NOT NULL,
                val1 NVARCHAR(100),
                val2 INT,
                val3 DATETIME
            );
        """,
        'index': None,
        'cond_col': 'id'
    },
    'Clustered Index (PK)': {
        'create': """
            CREATE TABLE [{table}] (
                id INT NOT NULL PRIMARY KEY CLUSTERED,
                val1 NVARCHAR(100),
                val2 INT,
                val3 DATETIME
            );
        """,
        'index': None,
        'cond_col': 'id'
    },
    'NC Index (PK)': {
        'create': """
            CREATE TABLE [{table}] (
                id INT NOT NULL PRIMARY KEY NONCLUSTERED,
                val1 NVARCHAR(100),
                val2 INT,
                val3 DATETIME
            );
        """,
        'index': None,
        'cond_col': 'id'
    },
    'NC Index (Non-PK)': {
        'create': """
            CREATE TABLE [{table}] (
                id INT NOT NULL,
                val1 NVARCHAR(100),
                val2 INT,
                val3 DATETIME
            );
        """,
        'index': "CREATE NONCLUSTERED INDEX IX_{table}_val2 ON [{table}](val2);",
        'cond_col': 'val2'
    },
    'CI + NC Index (val2)': {
        'create': """
            CREATE TABLE [{table}] (
                id INT NOT NULL PRIMARY KEY CLUSTERED,
                val1 NVARCHAR(100),
                val2 INT,
                val3 DATETIME
            );
        """,
        'index': "CREATE NONCLUSTERED INDEX IX_{table}_val2 ON [{table}](val2);",
        'cond_col': 'val2'
    }
}

# ── Helpers ───────────────────────────────────────────────────────────
def get_connection(database=None, autocommit=False):
    cs = (
        f"DRIVER={DRIVER};SERVER={SERVER};UID={UID};PWD={PWD};"
        f"TrustServerCertificate=yes;"
    )
    if database:
        cs += f"DATABASE={database};"
    return pyodbc.connect(cs, autocommit=autocommit)

def ensure_database():
    conn = get_connection(autocommit=True)
    cursor = conn.cursor()
    cursor.execute(f"IF NOT EXISTS (SELECT 1 FROM sys.databases WHERE name = '{DATABASE}') CREATE DATABASE [{DATABASE}];")
    conn.close()

def drop_table(cursor, table):
    cursor.execute(f"IF OBJECT_ID('{table}', 'U') IS NOT NULL DROP TABLE [{table}];")
    cursor.connection.commit()

def timed(func):
    start = time.perf_counter()
    func()
    return time.perf_counter() - start

def bulk_insert_batch(cursor, table, ids, batch_size=1000):
    sql = f"INSERT INTO [{table}] (id, val1, val2, val3) VALUES (?, ?, ?, ?)"
    for i in range(0, len(ids), batch_size):
        chunk = ids[i:i + batch_size]
        data = []
        for row_id in chunk:
            val2 = random.randint(1, 1_000_000)
            month = random.randint(1, 12)
            day = random.randint(1, 28)
            data.append((row_id, f'data_{row_id}', val2, f'2024-{month:02d}-{day:02d}'))
        cursor.executemany(sql, data)
        cursor.connection.commit()

def get_sample_keys(cursor, table, n, sample_count=SAMPLE_COUNT):
    ids = random.sample(range(1, n + 1), min(sample_count, n))
    placeholders = ",".join(["?"] * len(ids))
    cursor.execute(f"SELECT id, val2 FROM [{table}] WHERE id IN ({placeholders})", ids)
    rows = cursor.fetchall()
    return [r[0] for r in rows], [r[1] for r in rows]

# ── Benchmark functions ──────────────────────────────────────────────
def bench_insert(cursor, table, n):
    ids = list(range(1, n + 1))
    def do():
        bulk_insert_batch(cursor, table, ids)
    return timed(do)

def bench_update_unindexed(cursor, table, n, sample_count=SAMPLE_COUNT):
    ids = random.sample(range(1, n + 1), min(sample_count, n))
    val1_list = [f'data_{pk}' for pk in ids]
    sql = f"UPDATE [{table}] SET val3 = ? WHERE val1 = ?"
    data = [(f'2024-12-31', v1) for v1 in val1_list]
    def do():
        cursor.executemany(sql, data)
        cursor.connection.commit()
    return timed(do)

def bench_delete_unindexed(cursor, table, n, sample_count=SAMPLE_COUNT):
    ids = random.sample(range(1, n + 1), min(sample_count, n))
    val1_list = [f'data_{pk}' for pk in ids]
    sql = f"DELETE FROM [{table}] WHERE val1 = ?"
    data = [(v1,) for v1 in val1_list]
    def do():
        cursor.executemany(sql, data)
        cursor.connection.commit()
    return timed(do)

def bench_update_indexed(cursor, table, keys, cond_col):
    sql = f"UPDATE [{table}] SET val3 = ? WHERE {cond_col} = ?"
    data = [(f'2024-12-31', k) for k in keys]
    def do():
        cursor.executemany(sql, data)
        cursor.connection.commit()
    return timed(do)

def bench_delete_indexed(cursor, table, keys, cond_col):
    sql = f"DELETE FROM [{table}] WHERE {cond_col} = ?"
    data = [(k,) for k in keys]
    def do():
        cursor.executemany(sql, data)
        cursor.connection.commit()
    return timed(do)

# ── Main benchmark driver ────────────────────────────────────────────
def run_benchmarks():
    ensure_database()
    operations = ['Insert', 'Update (Unindexed)', 'Update (Indexed)', 'Delete (Unindexed)', 'Delete (Indexed)']
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

            safe_name = struct_name.replace(' ', '_').replace('+', 'P').replace('(', '').replace(')', '').replace('-', '')
            
            # --- Phase 1: Unindexed ---
            tbl_crud_unidx = f"b_cudu_{safe_name}_{size}"
            drop_table(cursor, tbl_crud_unidx)
            cursor.execute(struct_cfg['create'].format(table=tbl_crud_unidx))
            if struct_cfg['index']: cursor.execute(struct_cfg['index'].format(table=tbl_crud_unidx))
            cursor.connection.commit()
            
            t_ins = bench_insert(cursor, tbl_crud_unidx, size)
            results[struct_name]['Insert'].append(t_ins)
            print(f"  Insert              : {t_ins:.6f}s", flush=True)
            
            cursor.execute("DBCC DROPCLEANBUFFERS")
            t_upd_un = bench_update_unindexed(cursor, tbl_crud_unidx, size)
            results[struct_name]['Update (Unindexed)'].append(t_upd_un)
            print(f"  Update (Unindexed)  : {t_upd_un:.6f}s", flush=True)

            cursor.execute("DBCC DROPCLEANBUFFERS")
            t_del_un = bench_delete_unindexed(cursor, tbl_crud_unidx, size)
            results[struct_name]['Delete (Unindexed)'].append(t_del_un)
            print(f"  Delete (Unindexed)  : {t_del_un:.6f}s", flush=True)
            drop_table(cursor, tbl_crud_unidx)
            
            # --- Phase 2: Indexed ---
            tbl_crud_idx = f"b_cudi_{safe_name}_{size}"
            drop_table(cursor, tbl_crud_idx)
            cursor.execute(struct_cfg['create'].format(table=tbl_crud_idx))
            if struct_cfg['index']: cursor.execute(struct_cfg['index'].format(table=tbl_crud_idx))
            cursor.connection.commit()
            
            bench_insert(cursor, tbl_crud_idx, size) # Already timed in phase 1, just populate

            id_list, val2_list = get_sample_keys(cursor, tbl_crud_idx, size)
            keys = id_list if struct_cfg['cond_col'] == 'id' else val2_list

            cursor.execute("DBCC DROPCLEANBUFFERS")
            t_upd_idx = bench_update_indexed(cursor, tbl_crud_idx, keys, struct_cfg['cond_col'])
            results[struct_name]['Update (Indexed)'].append(t_upd_idx)
            print(f"  Update (Indexed)    : {t_upd_idx:.6f}s", flush=True)

            cursor.execute("DBCC DROPCLEANBUFFERS")
            t_del_idx = bench_delete_indexed(cursor, tbl_crud_idx, keys, struct_cfg['cond_col'])
            results[struct_name]['Delete (Indexed)'].append(t_del_idx)
            print(f"  Delete (Indexed)    : {t_del_idx:.6f}s", flush=True)
            drop_table(cursor, tbl_crud_idx)

    conn.close()
    return results, operations

# ── Visualization ─────────────────────────────────────────────────────
def plot_results(results, operations):
    colors = {
        'Pure Heap':             '#e74c3c',
        'Clustered Index (PK)':  '#2ecc71',
        'NC Index (PK)':         '#3498db',
        'NC Index (Non-PK)':     '#9b59b6',
        'CI + NC Index (val2)':  '#f1c40f',
    }
    markers = {
        'Pure Heap':             'o',
        'Clustered Index (PK)':  '^',
        'NC Index (PK)':         's',
        'NC Index (Non-PK)':     'D',
        'CI + NC Index (val2)':  'v',
    }

    # 1. Line charts
    fig, axes = plt.subplots(2, 3, figsize=(22, 12))
    fig.suptitle('CUD Performance Comparison: Unindexed vs Indexed', fontsize=18, fontweight='bold', y=0.98)
    
    for idx, op in enumerate(operations):
        ax = axes[idx // 3][idx % 3]
        for struct_name in STRUCTURES:
            times = results[struct_name][op]
            ax.plot(DATA_SIZES, times, marker=markers[struct_name], label=struct_name, color=colors[struct_name], linewidth=2.2, markersize=7)
            for x, y in zip(DATA_SIZES, times):
                ax.annotate(f'{y:.4f}s', (x, y), textcoords='offset points', xytext=(0, 10), fontsize=7, ha='center', fontweight='bold', color=colors[struct_name])
        ax.set_title(op, fontsize=13, fontweight='bold')
        ax.set_xlabel('Data Size (rows)')
        ax.set_ylabel('Time (seconds)')
        ax.legend(fontsize=9)
        ax.grid(True, alpha=0.3)
        ax.set_xscale('log')
        ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))
    
    axes[1][2].axis('off') # empty plot
    plt.tight_layout(rect=[0, 0, 1, 0.95])
    path1 = os.path.join(OUTPUT_DIR, 'cud_lines.png')
    plt.savefig(path1, dpi=150, bbox_inches='tight')
    plt.close()

    # 2. Bar chart
    fig2, axes2 = plt.subplots(1, len(DATA_SIZES), figsize=(26, 6), sharey=False)
    fig2.suptitle('CUD Performance by Data Size', fontsize=16, fontweight='bold', y=1.02)
    
    bar_width = 0.15
    struct_names = list(STRUCTURES.keys())
    
    for size_idx, size in enumerate(DATA_SIZES):
        ax = axes2[size_idx]
        x = np.arange(len(operations))
        for s_idx, struct_name in enumerate(struct_names):
            times = [results[struct_name][op][size_idx] for op in operations]
            bars = ax.bar(x + s_idx * bar_width, times, bar_width, label=struct_name, color=colors[struct_name], alpha=0.85)
            for bar, val in zip(bars, times):
                ax.text(bar.get_x() + bar.get_width()/2, bar.get_height(), f'{val:.4f}', ha='center', va='bottom', fontsize=5, fontweight='bold', rotation=45)
        ax.set_title(f'N = {size:,}', fontsize=12, fontweight='bold')
        ax.set_xticks(x + bar_width * 2)
        ax.set_xticklabels([op.replace(' ', '\n') for op in operations], fontsize=8)
        ax.set_ylabel('Time (s)')
        if size_idx == 0: ax.legend(fontsize=7, loc='upper left')
        ax.grid(axis='y', alpha=0.3)
    
    plt.tight_layout()
    path2 = os.path.join(OUTPUT_DIR, 'cud_bars.png')
    plt.savefig(path2, dpi=150, bbox_inches='tight')
    plt.close()

    # 3. Heatmap
    fig3, axes3 = plt.subplots(1, len(STRUCTURES), figsize=(6.5 * len(STRUCTURES), 5))
    fig3.suptitle('Heatmap: CUD Benchmark (seconds)', fontsize=16, fontweight='bold', y=1.04)
    
    for s_idx, struct_name in enumerate(struct_names):
        ax = axes3[s_idx]
        data = np.array([results[struct_name][op] for op in operations])
        im = ax.imshow(data, cmap='YlOrRd', aspect='auto')
        ax.set_xticks(range(len(DATA_SIZES)))
        ax.set_xticklabels([f'{s:,}' for s in DATA_SIZES], fontsize=9)
        ax.set_yticks(range(len(operations)))
        ax.set_yticklabels(operations, fontsize=9)
        ax.set_title(struct_name, fontsize=11, fontweight='bold', color=colors[struct_name])
        ax.set_xlabel('Data Size')
        for i in range(len(operations)):
            for j in range(len(DATA_SIZES)):
                val = data[i, j]
                ax.text(j, i, f'{val:.4f}s', ha='center', va='center', fontsize=8, fontweight='bold', color='white' if val > data.max()*0.6 else 'black')
        plt.colorbar(im, ax=ax, shrink=0.8)
    
    plt.tight_layout()
    path3 = os.path.join(OUTPUT_DIR, 'cud_heatmap.png')
    plt.savefig(path3, dpi=150, bbox_inches='tight')
    plt.close()

    return path1, path2, path3

def print_summary_tables(results, operations):
    for op in operations:
        print(f"\n{'─'*70}")
        print(f"  {op}")
        print(f"{'─'*70}")
        headers = ['Structure'] + [f'N={s:,}' for s in DATA_SIZES]
        rows = []
        for struct_name in STRUCTURES:
            row = [struct_name] + [f'{t:.6f}s' for t in results[struct_name][op]]
            rows.append(row)
        print(tabulate(rows, headers=headers, tablefmt='grid'))

if __name__ == '__main__':
    print("=" * 60)
    print("  CUD Benchmark: Index Usage Comparison")
    print("=" * 60)
    print(f"  Data sizes  : {DATA_SIZES}")
    print(f"  Structures  : {list(STRUCTURES.keys())}")
    print(f"  Sample count: {SAMPLE_COUNT} (for updates, deletes)")
    print()

    results, operations = run_benchmarks()

    print("\n\n" + "=" * 60)
    print("  BENCHMARK RESULTS SUMMARY")
    print("=" * 60)
    print_summary_tables(results, operations)

    charts = plot_results(results, operations)

    json_path = os.path.join(OUTPUT_DIR, 'cud_raw.json')
    json_data = {
        'data_sizes': DATA_SIZES,
        'sample_count': SAMPLE_COUNT,
        'results': {s: {op: times for op, times in ops.items()} for s, ops in results.items()},
    }
    with open(json_path, 'w') as f:
        json.dump(json_data, f, indent=2)
    print(f"\nRaw JSON saved: {json_path}")
    print("\n✅ Benchmark complete!")
