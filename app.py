from flask import Flask, render_template, request, redirect, session
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
    cursor.close()
    conn.close()
    return render_template('dashboard.html', tables=tables)

# ------------------- INSERT -------------------
@app.route('/insert/<table_name>', methods=['GET', 'POST'])
def insert_record(table_name):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(f"DESCRIBE {table_name};")
    columns = cursor.fetchall()
    message = None

    if request.method == 'POST':
        try:
            values = []
            placeholders = []
            for col in columns:
                col_name = col['Field']
                val = request.form.get(col_name)
                if val == "":
                    val = None
                values.append(val)
                placeholders.append("%s")

            insert_query = f"INSERT INTO {table_name} ({', '.join([c['Field'] for c in columns])}) VALUES ({', '.join(placeholders)})"
            cursor.execute(insert_query, values)
            conn.commit()
            message = "✅ Record inserted successfully!"
        except mysql.connector.Error as e:
            message = f"❌ MySQL Error: {e}"
            print(traceback.format_exc())

    cursor.close()
    conn.close()
    return render_template('insert_form.html', table_name=table_name, columns=columns, message=message)

# ------------------- QUERY EXECUTION -------------------
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

# ------------------- VIEW DATABASE -------------------
@app.route('/viewdb')
def view_database():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SHOW TABLES;")
    tables = [t[0] for t in cursor.fetchall()]
    cursor.close()
    conn.close()
    return render_template('view_table.html', tables=tables, selected_table=None)

@app.route('/viewdb/<table_name>')
def view_table(table_name):
    conn = get_connection()
    cursor = conn.cursor()

    # Custom queries (updated with your exact schema)
    queries = {
        "STANDINGS": """
            SELECT 
                standing_id AS 'Stance',
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

    # Choose proper query or default
    query = queries.get(table_name.upper(), f"SELECT * FROM {table_name}")
    cursor.execute(query)
    rows = cursor.fetchall()
    columns = [desc[0] for desc in cursor.description]
    cursor.close()
    conn.close()

    return render_template('view_table.html', tables=None, selected_table=table_name, columns=columns, rows=rows)


# ------------------- LOGOUT -------------------
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

if __name__ == '__main__':
    app.run(debug=True)
