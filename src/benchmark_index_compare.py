import pyodbc
import time
import random
import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

import json

# Configuration
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, 'db_config.json')
with open(CONFIG_PATH, 'r') as f:
    _db_config = json.load(f)

DRIVER = _db_config.get('DRIVER', '{ODBC Driver 18 for SQL Server}')
SERVER = _db_config.get('SERVER', 'localhost')
UID = _db_config['UID']
PWD = _db_config['PWD']
DATABASE = _db_config['DATABASE']

DATA_SIZES = [100, 1000, 2000, 5000]
OUTPUT_DIR = os.path.join(BASE_DIR, 'result')
os.makedirs(OUTPUT_DIR, exist_ok=True)

STRUCTURES = {
    'Non-Clustered Index': """
        CREATE TABLE {table} (
            id INT NOT NULL PRIMARY KEY NONCLUSTERED,
            val1 NVARCHAR(100),
            val2 INT
        );
    """,
    'Clustered Index': """
        CREATE TABLE {table} (
            id INT NOT NULL PRIMARY KEY CLUSTERED,
            val1 NVARCHAR(100),
            val2 INT
        );
    """
}

def get_connection(database=None):
    cs = f"DRIVER={DRIVER};SERVER={SERVER};UID={UID};PWD={PWD};TrustServerCertificate=yes;"
    if database:
        cs += f"DATABASE={database};"
    return pyodbc.connect(cs, autocommit=True)

def ensure_database():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"IF NOT EXISTS (SELECT 1 FROM sys.databases WHERE name = '{DATABASE}') CREATE DATABASE [{DATABASE}];")
    conn.close()

def drop_table(cursor, table):
    cursor.execute(f"IF OBJECT_ID('{table}', 'U') IS NOT NULL DROP TABLE [{table}];")

def timed(func):
    start = time.perf_counter()
    func()
    return time.perf_counter() - start

def bench_insert(cursor, table, n):
    sql = f"INSERT INTO [{table}] (id, val1, val2) VALUES (?, ?, ?)"
    # Generate data
    data = [(i, f'data_{i}', random.randint(1, 100000)) for i in range(1, n + 1)]
    # Shuffle for more realistic insert performance (avoid purely sequential page fills)
    random.shuffle(data)
    def do():
        for row in data:
            cursor.execute(sql, row)
    return timed(do)

def bench_read(cursor, table, n):
    sql = f"SELECT * FROM [{table}] WHERE id = ?"
    ids = list(range(1, n + 1))
    random.shuffle(ids)
    def do():
        for pk in ids:
            cursor.execute(sql, pk)
            cursor.fetchall()
    return timed(do)

def bench_update(cursor, table, n):
    sql = f"UPDATE [{table}] SET val1 = ?, val2 = ? WHERE id = ?"
    ids = list(range(1, n + 1))
    random.shuffle(ids)
    def do():
        for pk in ids:
            cursor.execute(sql, f'updated_{pk}', random.randint(1, 100000), pk)
    return timed(do)

def bench_delete(cursor, table, n):
    sql = f"DELETE FROM [{table}] WHERE id = ?"
    ids = list(range(1, n + 1))
    random.shuffle(ids)
    def do():
        for pk in ids:
            cursor.execute(sql, pk)
    return timed(do)

def run_benchmarks():
    ensure_database()
    conn = get_connection(DATABASE)
    cursor = conn.cursor()
    
    operations = ['Insert (C)', 'Read (R)', 'Update (U)', 'Delete (D)']
    results = {struct: {op: [] for op in operations} for struct in STRUCTURES}
    
    for size in DATA_SIZES:
        print(f"\n--- Benchmarking Data Size: {size} ---")
        for struct_name, create_sql in STRUCTURES.items():
            print(f"Testing {struct_name}...")
            table_name = f"bench_idx_{struct_name.replace(' ', '_').replace('-', '_')}_{size}"
            
            drop_table(cursor, table_name)
            cursor.execute(create_sql.format(table=table_name))
            
            # 1. Create (Insert)
            t_insert = bench_insert(cursor, table_name, size)
            results[struct_name]['Insert (C)'].append(t_insert)
            
            # Clear cache for fair read
            cursor.execute("DBCC DROPCLEANBUFFERS")
            
            # 2. Read (Select)
            t_read = bench_read(cursor, table_name, size)
            results[struct_name]['Read (R)'].append(t_read)
            
            # 3. Update
            t_update = bench_update(cursor, table_name, size)
            results[struct_name]['Update (U)'].append(t_update)
            
            # 4. Delete
            t_delete = bench_delete(cursor, table_name, size)
            results[struct_name]['Delete (D)'].append(t_delete)
            
            drop_table(cursor, table_name)
            
    conn.close()
    return results, operations

def plot_results(results, operations):
    fig, axes = plt.subplots(2, 2, figsize=(15, 10))
    fig.suptitle('CRUD Performance: Non-Clustered vs Clustered Index', fontsize=18, fontweight='bold')
    
    axes = axes.flatten()
    colors = {'Non-Clustered Index': '#3498db', 'Clustered Index': '#2ecc71'}
    markers = {'Non-Clustered Index': 'o', 'Clustered Index': 's'}
    
    for idx, op in enumerate(operations):
        ax = axes[idx]
        for struct_name in STRUCTURES:
            times = results[struct_name][op]
            ax.plot(DATA_SIZES, times, marker=markers[struct_name], color=colors[struct_name], label=struct_name, linewidth=2, markersize=8)
            for x, y in zip(DATA_SIZES, times):
                ax.annotate(f'{y:.3f}s', (x, y), textcoords="offset points", xytext=(0, 8), ha='center', fontsize=9, color=colors[struct_name], fontweight='bold')
                
        ax.set_title(op, fontsize=14, fontweight='bold')
        ax.set_xlabel('Data Size (rows)', fontsize=12)
        ax.set_ylabel('Time (seconds)', fontsize=12)
        ax.set_xticks(DATA_SIZES)
        ax.legend(fontsize=10)
        ax.grid(True, alpha=0.3)
        
    plt.tight_layout(rect=[0, 0, 1, 0.96])
    output_file = os.path.join(OUTPUT_DIR, 'index_comparison_crud.png')
    plt.savefig(output_file, dpi=150)
    print(f"\nGraph saved to: {output_file}")
    plt.close()

if __name__ == '__main__':
    print("Starting Benchmark: Non-Clustered Index vs Clustered Index")
    results, operations = run_benchmarks()
    plot_results(results, operations)
    print("Benchmarking completed successfully.")
