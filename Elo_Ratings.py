import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import json
import pandas as pd
import random
import datetime

creds_dict = st.secrets["gcp_service_account"]
scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
client = gspread.authorize(creds)

# ✅ Access Sheets directly without making read requests
elo_sheet = client.open_by_url("https://docs.google.com/spreadsheets/d/13yXfj4jC_AjKuvtPpdf9OeoZ-p_ifdTrw5Kw9w6afP4/edit").worksheet("Sheet1")
votes_sheet = client.open("Community Elo Ratings").worksheet("UserVotes")
value_sheet = client.open_by_url("https://docs.google.com/spreadsheets/d/1Qt7zriA6f696jAeXv3XvzdJPmml8QuplGU9fU-3-SRs/edit").worksheet("HPPR Rankings")


### ✅ **Functions That Use Only Google Sheet Data (No Read Requests)**

def get_players():
    """Instantly pulls all player data from Google Sheets without read requests."""
    all_values = elo_sheet.get_all_values()
    df = pd.DataFrame(all_values[1:], columns=all_values[0])  # First row is headers

    # Convert numeric columns
    df["elo"] = pd.to_numeric(df["elo"], errors="coerce").fillna(1500).astype(float)
    df["Votes"] = pd.to_numeric(df["Votes"], errors="coerce").fillna(0).astype(int)

    # Compute ranks in-memory instead of re-reading
    df["pos_rank"] = df.groupby("pos")["elo"].rank(method="min", ascending=False).astype(int)

    return df


def get_user_data():
    """Instantly pulls user vote data from Google Sheets without read requests."""
    all_values = votes_sheet.get_all_values()
    df = pd.DataFrame(all_values[1:], columns=all_values[0])  # First row is headers
    df["username"] = df["username"].str.lower()  # Normalize usernames to lowercase

    return df


def get_player_value(player_name):
    """Fetches player value from Google Sheets directly (no read requests)."""
    all_values = value_sheet.get_all_values()
    df = pd.DataFrame(all_values[1:], columns=all_values[0])

    player_row = df[df["Player Name"].str.lower() == player_name.lower()]
    return float(player_row["Value"].values[0]) if not player_row.empty else None


def update_user_vote(username, count_vote=True, user_data):
    """Updates user vote data in Google Sheets without extra read requests."""
    df = user_data  # Use preloaded data
    username_lower = username.lower()

    user_row = df[df["username"] == username_lower]
    today = datetime.date.today().strftime("%Y-%m-%d")

    updates = []
    if user_row.empty:
        votes_sheet.append_row([username, 1 if count_vote else 0, 1 if count_vote else 0, today])
        return

    row_idx = user_row.index[0] + 2  # Adjust for Google Sheets indexing
    total_votes_col, weekly_votes_col, last_voted_col = 2, 3, 4

    # Get current values
    user_current_total = int(df.loc[user_row.index[0], "total_votes"])
    user_current_weekly = int(df.loc[user_row.index[0], "weekly_votes"])
    user_last_voted = df.loc[user_row.index[0], "last_voted"]

    # Reset weekly votes on Monday
    if datetime.datetime.today().weekday() == 0 and user_last_voted != today:
        updates.append({"range": f"R{row_idx}C{weekly_votes_col}", "values": [[0]]})

    if count_vote:
        updates.append({"range": f"R{row_idx}C{total_votes_col}", "values": [[user_current_total + 1]]})
        updates.append({"range": f"R{row_idx}C{weekly_votes_col}", "values": [[user_current_weekly + 1]]})

    updates.append({"range": f"R{row_idx}C{last_voted_col}", "values": [[today]]})

    if updates:
        votes_sheet.batch_update(updates)


def update_google_sheet(player1_name, player1_new_elo, player2_name, player2_new_elo, player_data):
    """Updates player Elo and Votes in Google Sheets without extra read requests."""
    df = player_data
    elo_col_index = df.columns.tolist().index("elo") + 1
    votes_col_index = df.columns.tolist().index("Votes") + 1 if "Votes" in df.columns else None

    player1_row = df[df["name"].str.lower() == player1_name.lower()].index[0] + 2
    player2_row = df[df["name"].str.lower() == player2_name.lower()].index[0] + 2

    updates = []
    updates.append({"range": f"R{player1_row}C{elo_col_index}", "values": [[float(player1_new_elo)]]})
    updates.append({"range": f"R{player2_row}C{elo_col_index}", "values": [[float(player2_new_elo)]]})

    if votes_col_index:
        updates.append({"range": f"R{player1_row}C{votes_col_index}", "values": [[df.loc[player1_row - 2, "Votes"] + 1]]})
        updates.append({"range": f"R{player2_row}C{votes_col_index}", "values": [[df.loc[player2_row - 2, "Votes"] + 1]]})

    elo_sheet.batch_update(updates)


### ✅ **Process Vote (Now Uses Preloaded Data)**
def process_vote(selected_player):
    with st.status("Submitting your pick and adjusting the rankings! ⏳", expanded=False) as status:
        player_data = get_players()  # ✅ Load players from the Google Sheet
        user_data = get_user_data()  # ✅ Load user data from the Google Sheet

        if selected_player == player1["name"]:
            new_elo1, new_elo2 = calculate_elo(player1["elo"], player2["elo"])
        else:
            new_elo2, new_elo1 = calculate_elo(player2["elo"], player1["elo"])

        # ✅ Now passing `player_data` correctly to avoid the TypeError
        update_google_sheet(player1["name"], new_elo1, player2["name"], new_elo2, player_data)
        update_user_vote(st.session_state["username"], count_vote=True, user_data=user_data)

        st.session_state["updated_elo"] = {player1["name"]: new_elo1, player2["name"]: new_elo2}
        st.session_state["selected_player"] = selected_player

        status.update(label="✅ Pick Submitted! Rankings Updated.", state="complete")



def get_player_elo(player_name):
    """Fetches the Elo rating for a given player from cached player data."""
    try:
        df = st.session_state["players_cache"]  # ✅ Use cached players data

        # Find player and get their Elo
        player_row = df[df["name"].str.lower() == player_name.lower()]
        if not player_row.empty:
            return float(player_row["elo"].values[0])  # Convert Elo to float
        else:
            return None  # Player not found

    except Exception as e:
        st.error(f"❌ Error fetching player Elo: {e}")
        return None

# Elo Calculation (Moved Above Process_Vote)
def calculate_elo(winner_elo, loser_elo, k=24):
    expected_winner = 1 / (1 + 10 ** ((loser_elo - winner_elo) / 400))
    expected_loser = 1 / (1 + 10 ** ((winner_elo - loser_elo) / 400))
    new_winner_elo = winner_elo + k * (1 - expected_winner)
    new_loser_elo = loser_elo + k * (0 - expected_loser)
    return round(new_winner_elo), round(new_loser_elo)

def aggressive_weighted_selection(df, weight_col="elo", alpha=6):
    df = df.copy()  # ✅ Ensure modifications are on a separate copy

    max_elo = df[weight_col].max()
    min_elo = df[weight_col].min()
    
    # Normalize Elo scores to avoid extreme weighting
    df.loc[:, "normalized_elo"] = (df[weight_col] - min_elo) / (max_elo - min_elo)
    
    # Exponentiate with alpha for stronger weighting on higher Elo players
    df.loc[:, "weight"] = df["normalized_elo"] ** alpha
    
    # Normalize weights to sum to 1 (softmax-like effect)
    df.loc[:, "weight"] = df["weight"] / df["weight"].sum()
    
    # Select based on weighted probability
    selected_index = random.choices(df.index, weights=df["weight"], k=1)[0]
    return df.loc[selected_index]

def display_player(player, col):
    with col:
        st.markdown(
            f'<div style="padding: 10px; border-radius: 10px; text-align: center;">'
            f'<img src="{player["image_url"]}" width="150" style="display: block; margin: auto;">'
            f'<p style="margin-top: 10px; font-size: 16px; text-align: center;">{player["name"]} ({player["team"]} | {player["pos"]})</p>'
            f'</div>',
            unsafe_allow_html=True
        )

        if st.button("Draft", key=f"{player['name']}_{col}", use_container_width=True):
            process_vote(player["name"])

if "players_cache" not in st.session_state:
    st.session_state["players_cache"] = get_players()  # ✅ Load players ONCE per session
players = st.session_state["players_cache"]

# Initialize session state variables
if "player1" not in st.session_state or "player2" not in st.session_state:
    st.session_state.player1 = aggressive_weighted_selection(players)
    st.session_state.player2_candidates = players[
        (players["elo"] > st.session_state.player1["elo"] - 50) & (players["elo"] < st.session_state.player1["elo"] + 50)
    ]
    st.session_state.player2 = aggressive_weighted_selection(st.session_state.player2_candidates) if not st.session_state.player2_candidates.empty else aggressive_weighted_selection(players)

# Ensure initial_elo is always initialized
if "initial_elo" not in st.session_state:
    st.session_state["initial_elo"] = {}

# Update the initial Elo for the current matchup if not already stored
if st.session_state.player1["name"] not in st.session_state["initial_elo"]:
    st.session_state["initial_elo"][st.session_state.player1["name"]] = st.session_state.player1["elo"]
if st.session_state.player2["name"] not in st.session_state["initial_elo"]:
    st.session_state["initial_elo"][st.session_state.player2["name"]] = st.session_state.player2["elo"]

if "selected_player" not in st.session_state:
    st.session_state["selected_player"] = None

if "updated_elo" not in st.session_state:
    st.session_state["updated_elo"] = {}

player1 = st.session_state.player1
player2 = st.session_state.player2

# ✅ Fetch Elo instantly instead of API calls
player1_elo = get_player_elo(player1["name"])
player2_elo = get_player_elo(player2["name"])

# ✅ Fetch Value from HPPR Sheet (like before)
player1_value = get_player_value(player1["name"])
player2_value = get_player_value(player2["name"])

# ✅ Determine Nick's Pick (Highest Value with Elo Tie-Breaker)
if player1_value is not None and player2_value is not None:
    if player1_value > player2_value:
        nicks_pick = player1["name"]
    elif player2_value > player1_value:
        nicks_pick = player2["name"]
    else:  # Tie-breaker using Elo
        nicks_pick = player1["name"] if player1_elo > player2_elo else player2["name"]
else:
    nicks_pick = "N/A"  # Default if values are missing

# ✅ Username Input (No Extra Vote Count Here)
st.markdown("<h3 style='text-align: center;'>Enter Your Username to Track Your Rank:</h3>", unsafe_allow_html=True)
username = st.text_input("Username", value=st.session_state.get("username", ""), max_chars=15)

if username and "username" not in st.session_state:
    st.session_state["username"] = username
    update_user_vote(username, count_vote=False)  # ✅ Only track user, don't count extra vote

# Streamlit UI
st.markdown("<h1 style='text-align: center;'>Who Would You Rather Draft?</h1>", unsafe_allow_html=True)

col1, col2 = st.columns(2)

display_player(player1, col1)
display_player(player2, col2)

# Show updated Elo values if selection was made
if st.session_state["selected_player"]:
    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown("<h3 style='text-align: center;'>FFA Community Elo Ratings</h3>", unsafe_allow_html=True)

    # Gather player data and sort by ELO (highest first)
    player_data = [
        {
            "name": player1["name"],
            "elo": st.session_state["updated_elo"].get(player1["name"], player1["elo"]),
            "change": st.session_state["updated_elo"].get(player1["name"], player1["elo"]) - st.session_state["initial_elo"].get(player1["name"], player1["elo"]),
            "pos": player1["pos"],
            "rank": players.loc[players["name"] == player1["name"], "pos_rank"].values[0]
        },
        {
            "name": player2["name"],
            "elo": st.session_state["updated_elo"].get(player2["name"], player2["elo"]),
            "change": st.session_state["updated_elo"].get(player2["name"], player2["elo"]) - st.session_state["initial_elo"].get(player2["name"], player2["elo"]),
            "pos": player2["pos"],
            "rank": players.loc[players["name"] == player2["name"], "pos_rank"].values[0]
        }
    ]

    # Sort players by ELO (highest first)
    player_data.sort(key=lambda x: x["elo"], reverse=True)

    # Display updated ELOs with sorting & highlighting
    for player in player_data:
        color = "green" if player["change"] > 0 else "red"
        change_text = f"<span style='color:{color};'>({player['change']:+})</span>"

        # Check if this is the selected player and highlight background
        background_style = "background-color: yellow; padding: 5px; border-radius: 5px;" if player["name"] == st.session_state["selected_player"] else ""

        st.markdown(
            f"<div style='{background_style} font-size:18px; padding: 5px;'>"
            f"<b>{player['name']}</b>: {player['elo']} ELO {change_text} | <b>{player['pos']} Rank:</b> {player['rank']}"
            f"</div>",
            unsafe_allow_html=True
        )
    # ✅ Check if Nick’s Pick Matches the User’s Pick
    match_icon = "✅" if nicks_pick == st.session_state["selected_player"] else "❌"
    
    # ✅ Display Nick's Pick with Padding and Match Indicator
    st.markdown(
        f"<div style='font-size:18px; padding: 5px;'>"
        f"<b>Nick Would Have Picked:</b> {nicks_pick} {match_icon}"
        f"</div>",
        unsafe_allow_html=True
    )

    # ✅ Load leaderboard data
    df = get_user_data()
    
    # 🏆 All-Time Leaderboard (Sorted by All Time Votes)
    st.markdown("## 🏆 All-Time Leaderboard (Total Votes)")
    df_all_time = df.copy().sort_values(by="total_votes", ascending=False).head(5)  # Sort by all time votes
    df_all_time["Rank"] = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"][: len(df_all_time)]  # Assign ranking icons
    df_all_time = df_all_time.rename(
        columns={
            "username": "Username",
            "total_votes": "All Time Votes",
            "last_voted": "Last Voted"
        }
    )  # ✅ Rename columns
    df_all_time = df_all_time[["Rank", "Username", "All Time Votes", "Last Voted"]]  # ✅ Remove Weekly Votes
    st.dataframe(df_all_time.set_index("Rank"), hide_index=False, use_container_width=True)
    
    # ⏳ Weekly Leaderboard (Sorted by Weekly Votes)
    st.markdown("## ⏳ Weekly Leaderboard (Resets on Monday)")
    df_weekly = df.copy().sort_values(by="weekly_votes", ascending=False).head(5)  # Sort by weekly votes
    df_weekly["Rank"] = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"][: len(df_weekly)]  # Assign ranking icons
    df_weekly = df_weekly.rename(
        columns={
            "username": "Username",
            "weekly_votes": "Weekly Votes",
            "last_voted": "Last Voted"
        }
    )  # ✅ Rename columns
    df_weekly = df_weekly[["Rank", "Username", "Weekly Votes", "Last Voted"]]  # ✅ Remove All Time Votes
    st.dataframe(df_weekly.set_index("Rank"), hide_index=False, use_container_width=True)

    # "Next Matchup" button appears here, after Elo ratings are shown
    st.markdown("<div style='text-align: center; margin-top: 20px;'>", unsafe_allow_html=True)

    if st.button("Next Matchup", key="next_matchup", use_container_width=True):
        with st.status("Loading next matchup... ⏳", expanded=False) as status:
            # ✅ Select new Player 1 instantly from cached players
            st.session_state["player1"] = aggressive_weighted_selection(players)
    
            # ✅ Filter player2_candidates only if Player 1 has changed
            if "last_player1" not in st.session_state or st.session_state["last_player1"] != st.session_state["player1"]["name"]:
                st.session_state["player2_candidates"] = players[
                    (players["elo"] > st.session_state["player1"]["elo"] - 50) &
                    (players["elo"] < st.session_state["player1"]["elo"] + 50)
                ]
                st.session_state["last_player1"] = st.session_state["player1"]["name"]  # ✅ Track last Player 1
    
            # ✅ Select Player 2 instantly
            st.session_state["player2"] = aggressive_weighted_selection(st.session_state["player2_candidates"]) if not st.session_state["player2_candidates"].empty else aggressive_weighted_selection(players)
    
            # ✅ Store Elo data in session state
            st.session_state["initial_elo"] = {
                st.session_state["player1"]["name"]: st.session_state["player1"]["elo"],
                st.session_state["player2"]["name"]: st.session_state["player2"]["elo"]
            }
    
            # ✅ Reset selected player and Elo values for new matchup
            st.session_state["selected_player"] = None
            st.session_state["updated_elo"] = {}
    
            status.update(label="✅ Next Matchup Ready!", state="complete")
    
        # ✅ Force rerun AFTER status update
        st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)
