from flask import Flask, render_template, request, redirect, session, url_for
import mysql.connector
import traceback

app = Flask(__name__)
app.secret_key = "supersecretkey"

# ------------------- Helper -------------------
def get_connection(db=True):
    if 'user' not in session:
        return None
    return mysql.connector.connect(
        host="localhost",
        user=session['user'],
        password=session['password'],
        database="cricket_league" if db else None
    )

def describe_table(cursor, table_name):
    cursor.execute(f"DESCRIBE {table_name};")
    return cursor.fetchall()  # list of tuples/dicts depending on cursor

def get_primary_key(columns):
    # columns is list of dicts or tuples; normalize
    for col in columns:
        # if using dictionary cursor:
        if isinstance(col, dict):
            if col.get('Key') == 'PRI':
                return col['Field']
        else:
            # tuple result format: Field, Type, Null, Key, Default, Extra
            if col[3] == 'PRI':
                return col[0]
    return None

def is_auto_increment(col):
    if isinstance(col, dict):
        return 'auto_increment' in (col.get('Extra') or '')
    else:
        return 'auto_increment' in (col[5] or '')

# ------------------- LOGIN -------------------
@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = request.form['user']
        password = request.form['password']
        try:
            conn = mysql.connector.connect(host="localhost", user=user, password=password)
            cursor = conn.cursor()
            cursor.execute("USE cricket_league;")
            session['user'] = user
            session['password'] = password
            cursor.close()
            conn.close()
            return redirect('/dashboard')
        except mysql.connector.Error as e:
            return render_template('login.html', message=f"❌ Login failed: {e}")
    return render_template('login.html')

# ------------------- DASHBOARD -------------------
@app.route('/dashboard')
def dashboard():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SHOW TABLES;")
    tables = [t[0] for t in cursor.fetchall()]

    # ---------- Fetch stats dynamically ----------
    stats = {}
    try:
        # Most runs
        cursor.execute("""
            SELECT P.player_name, SUM(PS.runs_scored) AS runs
            FROM PLAYER_STATS PS
            JOIN PLAYERS P ON PS.player_id = P.player_id
            GROUP BY PS.player_id
            ORDER BY runs DESC;
        """)
        stats['runs'] = cursor.fetchall()

        # Most wickets
        cursor.execute("""
            SELECT P.player_name, SUM(PS.wickets_taken) AS wickets
            FROM PLAYER_STATS PS
            JOIN PLAYERS P ON PS.player_id = P.player_id
            GROUP BY PS.player_id
            ORDER BY wickets DESC;
        """)
        stats['wickets'] = cursor.fetchall()

        # Most boundaries
        cursor.execute("""
            SELECT P.player_name, SUM(PS.boundaries) AS boundaries
            FROM PLAYER_STATS PS
            JOIN PLAYERS P ON PS.player_id = P.player_id
            GROUP BY PS.player_id
            ORDER BY boundaries DESC;
        """)
        stats['boundaries'] = cursor.fetchall()
    except Exception as e:
        print("Stats error:", e)
        stats = {'runs': [], 'wickets': [], 'boundaries': []}

    cursor.close()
    conn.close()
    return render_template('dashboard.html', tables=tables, stats=stats)


# ------------------- TABLE ACTIONS (shows CRUD options) -------------------
@app.route('/table/<table_name>')
def table_actions(table_name):
    # simple page with 4 buttons: Create, Read, Update, Delete
    return render_template('table_actions.html', table_name=table_name)

# ------------------- READ (raw table without joins) -------------------
@app.route('/table/<table_name>/read')
def table_read(table_name):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(f"SELECT * FROM {table_name};")
        rows = cursor.fetchall()
        columns = [d[0] for d in cursor.description] if cursor.description else []
    except mysql.connector.Error as e:
        rows = []
        columns = []
        message = f"❌ MySQL Error: {e}"
        cursor.close()
        conn.close()
        return render_template('view_table.html', tables=None, selected_table=table_name, columns=columns, rows=rows, message=message)
    cursor.close()
    conn.close()
    return render_template('table_read.html', table_name=table_name, columns=columns, rows=rows)


# ------------------- INSERT (per-table insert form; skip auto_increment fields except PLAYERS.player_id allowed) -------------------
@app.route('/table/<table_name>/insert', methods=['GET', 'POST'])
def table_insert(table_name):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(f"DESCRIBE {table_name};")
    columns = cursor.fetchall()  # list of dicts: Field, Type, Null, Key, Default, Extra
    message = None

    # For insert form: exclude auto_increment fields *except* if it's PLAYERS and field is player_id (your request)
    insert_columns = []
    for col in columns:
        if is_auto_increment(col):
            continue
        # special allowance: user wanted id to be taken for players table
        if table_name.upper() == "PLAYERS" and col['Field'].lower() == "player_id":
            insert_columns.append(col)
        elif col['Field'].lower().endswith("_id") and col['Key'] == 'PRI' and table_name.upper() != "PLAYERS":
            # primary key that is NOT PLAYERS.player_id (and likely auto_increment): skip
            # Note: if user has a manual PK that is not auto_increment and not player_id, we include it
            if not is_auto_increment(col):
                insert_columns.append(col)
        else:
            insert_columns.append(col)

    if request.method == 'POST':
        try:
            values = []
            placeholders = []
            fields_to_insert = []
            for col in insert_columns:
                col_name = col['Field']
                val = request.form.get(col_name)
                if val == "":
                    val = None
                # do minimal type handling: let MySQL handle conversion; pass None for empty
                values.append(val)
                placeholders.append("%s")
                fields_to_insert.append(col_name)

            insert_query = f"INSERT INTO {table_name} ({', '.join(fields_to_insert)}) VALUES ({', '.join(placeholders)})"
            cursor.execute(insert_query, values)
            conn.commit()
            message = "✅ Record inserted successfully!"
        except mysql.connector.Error as e:
            message = f"❌ MySQL Error: {e}"
            print(traceback.format_exc())

    cursor.close()
    conn.close()
    return render_template('insert_form.html', table_name=table_name, columns=insert_columns, message=message)

# ------------------- UPDATE (choose PK, then edit) -------------------
@app.route('/table/<table_name>/update', methods=['GET', 'POST'])
def table_update(table_name):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(f"DESCRIBE {table_name};")
    columns = cursor.fetchall()
    pk = get_primary_key(columns)
    message = None

    # Step 1: if no pk, disallow (rare)
    if not pk:
        cursor.close()
        conn.close()
        return render_template('error.html', message=f"No primary key found for {table_name}; update not supported.")

    # Step A: GET show list of PK values to choose from
    if request.method == 'GET':
        cursor2 = conn.cursor()
        cursor2.execute(f"SELECT {pk} FROM {table_name} LIMIT 500;")
        pk_rows = [r[0] for r in cursor2.fetchall()]

        # also fetch table contents
        cursor3 = conn.cursor()
        cursor3.execute(f"SELECT * FROM {table_name} LIMIT 200;")
        rows = cursor3.fetchall()
        columns = [desc[0] for desc in cursor3.description] if cursor3.description else []

        cursor2.close()
        cursor3.close()
        cursor.close()
        conn.close()
        return render_template('update_select.html', table_name=table_name, pk=pk,
            pk_rows=pk_rows, rows=rows, columns=columns)


    # POST: either user selected id (show form) or submitted update payload
    action = request.form.get('action')
    if action == 'select_row':
        selected_id = request.form.get('selected_id')
        # fetch row data
        cursor.execute(f"SELECT * FROM {table_name} WHERE {pk} = %s LIMIT 1;", (selected_id,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        # Build editable columns: do not allow editing auto_increment PK (unless PLAYERS.player_id - then allow)
        editable_cols = []
        for col in columns:
            if col['Field'] == pk:
                if table_name.upper() == "PLAYERS" and pk.lower() == "player_id":
                    editable_cols.append(col)
                else:
                    # PK shown but not editable
                    continue
            else:
                editable_cols.append(col)
        return render_template('update_form.html', table_name=table_name, pk=pk, row=row, columns=editable_cols)

    elif action == 'do_update':
        selected_id = request.form.get('selected_id')
        # gather fields to update (all non-pk columns except disallowed auto_increment)
        set_clauses = []
        values = []
        for col in columns:
            fname = col['Field']
            if fname == pk and not (table_name.upper() == "PLAYERS" and pk.lower() == "player_id"):
                continue
            # if field is auto_increment and not allowed, skip
            if is_auto_increment(col) and not (table_name.upper() == "PLAYERS" and fname.lower() == "player_id"):
                continue
            # get value from form only if present
            if fname in request.form:
                val = request.form.get(fname)
                if val == "":
                    val = None
                set_clauses.append(f"{fname} = %s")
                values.append(val)
        if not set_clauses:
            message = "❌ No updatable fields were provided."
        else:
            values.append(selected_id)
            update_query = f"UPDATE {table_name} SET {', '.join(set_clauses)} WHERE {pk} = %s;"
            try:
                cur = conn.cursor()
                cur.execute(update_query, values)
                conn.commit()
                cur.close()
                message = "✅ Record updated successfully!"
            except mysql.connector.Error as e:
                message = f"❌ MySQL Error: {e}"
                print(traceback.format_exc())
        cursor.close()
        conn.close()
        return render_template('update_result.html', table_name=table_name, message=message)

    # fallback
    cursor.close()
    conn.close()
    return redirect(url_for('table_actions', table_name=table_name))

# ------------------- DELETE (choose PK and delete) -------------------
@app.route('/table/<table_name>/delete', methods=['GET', 'POST'])
def table_delete(table_name):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(f"DESCRIBE {table_name};")
    columns = cursor.fetchall()
    pk = get_primary_key(columns)
    message = None

    if not pk:
        cursor.close()
        conn.close()
        return render_template('error.html', message=f"No primary key found for {table_name}; delete not supported.")

    if request.method == 'GET':
        cursor2 = conn.cursor()
        cursor2.execute(f"SELECT {pk} FROM {table_name} LIMIT 500;")
        pk_rows = [r[0] for r in cursor2.fetchall()]

        # fetch table preview
        cursor3 = conn.cursor()
        cursor3.execute(f"SELECT * FROM {table_name} LIMIT 200;")
        rows = cursor3.fetchall()
        columns = [desc[0] for desc in cursor3.description] if cursor3.description else []

        cursor2.close()
        cursor3.close()
        cursor.close()
        conn.close()
        return render_template('delete_select.html', table_name=table_name, pk=pk,
            pk_rows=pk_rows, rows=rows, columns=columns)


    # POST
    selected_id = request.form.get('selected_id')
    confirm = request.form.get('confirm')
    if not confirm:
        # show a confirmation page
        cursor.execute(f"SELECT * FROM {table_name} WHERE {pk} = %s LIMIT 1;", (selected_id,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return render_template('delete_confirm.html', table_name=table_name, pk=pk, row=row, selected_id=selected_id)
    else:
        try:
            cur = conn.cursor()
            cur.execute(f"DELETE FROM {table_name} WHERE {pk} = %s;", (selected_id,))
            conn.commit()
            cur.close()
            message = "✅ Record deleted successfully!"
        except mysql.connector.Error as e:
            message = f"❌ MySQL Error: {e}"
            print(traceback.format_exc())
        cursor.close()
        conn.close()
        return render_template('delete_result.html', table_name=table_name, message=message)

# ------------------- VIEW DATABASE (existing) -------------------
@app.route('/viewdb')
def view_database():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SHOW TABLES;")
    tables = [t[0] for t in cursor.fetchall()]
    cursor.close()
    conn.close()
    return render_template('view_table.html', tables=tables, selected_table=None)

# ------------------- VIEW TABLE WITH YOUR CUSTOM QUERIES (existing logic preserved) -------------------
@app.route('/viewdb/<table_name>')
def view_table(table_name):
    conn = get_connection()
    cursor = conn.cursor()

    # Custom queries (kept from original file)
    queries = {
        "STANDINGS": """
            SELECT 
                ROW_NUMBER() OVER (ORDER BY S.points DESC, S.net_run_rate DESC) AS 'Rank',
                T.team_name AS 'Team Name',
                matches_played AS 'Matches Played',
                wins AS 'Wins',
                losses AS 'Losses',
                ties AS 'Ties',
                points AS 'Points',
                net_run_rate AS 'Net Run Rate'
            FROM STANDINGS S
            JOIN TEAMS T ON S.team_id = T.team_id
            ORDER BY points DESC;
        """,

        "PLAYER_STATS": """
            SELECT 
                P.player_name AS 'Player Name',
                P.role AS 'Role',
                P.batting_style AS 'Batting Style',
                P.bowling_style AS 'Bowling Style',
                runs_scored AS 'Runs Scored',
                wickets_taken AS 'Wickets Taken',
                boundaries AS 'Boundaries',
                T.team_name AS 'Team'
            FROM PLAYER_STATS PS
            JOIN PLAYERS P ON PS.player_id = P.player_id
            JOIN TEAMS T ON P.team_id = T.team_id
            ORDER BY T.team_name, P.player_name;
        """,

        "MATCH_RESULTS": """
            SELECT 
                MR.result_id AS 'Result ID',
                M.match_id AS 'Match ID',
                H.team_name AS 'Home Team',
                A.team_name AS 'Away Team',
                W.team_name AS 'Winner Team',
                P.player_name AS 'Man of the Match'
            FROM MATCH_RESULTS MR
            JOIN MATCHES M ON MR.match_id = M.match_id
            JOIN TEAMS H ON M.home_team_id = H.team_id
            JOIN TEAMS A ON M.away_team_id = A.team_id
            LEFT JOIN TEAMS W ON MR.winner_team_id = W.team_id
            JOIN PLAYERS P ON MR.man_of_the_match = P.player_id
            ORDER BY M.match_id;
        """,

        "MATCHES": """
            SELECT 
                M.match_id AS 'Match ID',
                H.team_name AS 'Home Team',
                A.team_name AS 'Away Team',
                M.status AS 'Status',
                M.match_type AS 'Match Type',
                M.match_date AS 'Match Date',
                V.venue_name AS 'Venue'
            FROM MATCHES M
            JOIN TEAMS H ON M.home_team_id = H.team_id
            JOIN TEAMS A ON M.away_team_id = A.team_id
            JOIN VENUES V ON M.venue_id = V.venue_id
            ORDER BY M.match_date;
        """,

        "PLAYERS": """
            SELECT 
                player_name AS 'Player Name',
                DOB AS 'Date of Birth',
                role AS 'Role',
                batting_style AS 'Batting Style',
                bowling_style AS 'Bowling Style',
                T.team_name AS 'Team'
            FROM PLAYERS P
            JOIN TEAMS T ON P.team_id = T.team_id
            ORDER BY T.team_name;
        """,

        "PLAYERS_CONTACTS": """
            SELECT 
                P.player_name AS 'Player Name',
                contact_no AS 'Contact Number',
                T.team_name AS 'Team'
            FROM PLAYERS_CONTACTS PC
            JOIN PLAYERS P ON PC.player_id = P.player_id
            JOIN TEAMS T ON P.team_id = T.team_id
            ORDER BY T.team_name;
        """,

        "TEAMS": """
            SELECT 
                team_name AS 'Team Name',
                coach_name AS 'Coach Name',
                home_city AS 'Home City'
            FROM TEAMS;
        """,

        "VENUES": """
            SELECT 
                venue_name AS 'Venue Name',
                city AS 'City',
                capacity AS 'Capacity'
            FROM VENUES;
        """
    }

    query = queries.get(table_name.upper(), f"SELECT * FROM {table_name}")
    cursor.execute(query)
    rows = cursor.fetchall()
    columns = [desc[0] for desc in cursor.description]
    cursor.close()
    conn.close()

    return render_template('view_table.html', tables=None, selected_table=table_name, columns=columns, rows=rows)

# ------------------- SQL QUERY EXECUTION (existing) -------------------
@app.route('/query', methods=['GET', 'POST'])
def query():
    if 'user' not in session:
        return redirect('/')

    result = None
    columns = None
    message = None
    query_text = ""

    if request.method == 'POST':
        query_text = request.form['sql']
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(query_text)

            if query_text.strip().lower().startswith(("select", "show", "desc", "describe")):
                result = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
            else:
                conn.commit()
                message = "✅ Query executed successfully!"
            cursor.close()
            conn.close()
        except mysql.connector.Error as e:
            message = f"❌ MySQL Error: {e}"
        except Exception as e:
            message = f"❌ Error: {e}"

    return render_template('query.html', result=result, columns=columns, message=message, query_text=query_text)

# ------------------- STATS (new) -------------------
@app.route('/stats')
def stats():
    conn = get_connection()
    cursor = conn.cursor()
    stats = {}
    try:
        # top run scorer
        cursor.execute("""
            SELECT P.player_name, SUM(PS.runs_scored) AS total_runs
            FROM PLAYER_STATS PS
            JOIN PLAYERS P ON PS.player_id = P.player_id
            GROUP BY PS.player_id
            ORDER BY total_runs DESC
            LIMIT 1;
        """)
        r = cursor.fetchone()
        stats['top_scorer'] = {'player': r[0], 'runs': int(r[1])} if r else None

        # top wicket taker
        cursor.execute("""
            SELECT P.player_name, SUM(PS.wickets_taken) AS total_wkts
            FROM PLAYER_STATS PS
            JOIN PLAYERS P ON PS.player_id = P.player_id
            GROUP BY PS.player_id
            ORDER BY total_wkts DESC
            LIMIT 1;
        """)
        r = cursor.fetchone()
        stats['top_bowler'] = {'player': r[0], 'wickets': int(r[1])} if r else None

        # most boundaries
        cursor.execute("""
            SELECT P.player_name, SUM(PS.boundaries) AS total_boundaries
            FROM PLAYER_STATS PS
            JOIN PLAYERS P ON PS.player_id = P.player_id
            GROUP BY PS.player_id
            ORDER BY total_boundaries DESC
            LIMIT 1;
        """)
        r = cursor.fetchone()
        stats['top_boundaries'] = {'player': r[0], 'boundaries': int(r[1])} if r else None

        # team runs aggregate
        cursor.execute("""
            SELECT T.team_name, COALESCE(SUM(PS.runs_scored),0) AS team_runs
            FROM TEAMS T
            LEFT JOIN PLAYERS P ON P.team_id = T.team_id
            LEFT JOIN PLAYER_STATS PS ON PS.player_id = P.player_id
            GROUP BY T.team_id
            ORDER BY team_runs DESC
            LIMIT 3;
        """)
        rows = cursor.fetchall()
        stats['top_teams_by_runs'] = [{'team': r[0], 'runs': int(r[1])} for r in rows]

    except mysql.connector.Error as e:
        stats['error'] = str(e)

    cursor.close()
    conn.close()
    return render_template('stats.html', stats=stats)

# ------------------- LOGOUT -------------------
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

if __name__ == '__main__':
    app.run(debug=True)
