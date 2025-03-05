import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import json
import pandas as pd
import random
import datetime
import time

creds_dict = st.secrets["gcp_service_account"]
scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
client = gspread.authorize(creds)  # ✅ Authorizing gspread
sheet = client.open("Community Elo Ratings").worksheet("Sheet1")  # Ensure correct sheet name
# ✅ New sheet reference for tracking user votes
votes_sheet = client.open("Community Elo Ratings").worksheet("UserVotes")  # Ensure "UserVotes" exists
# ✅ New sheet reference for Nick's pick logic
value_sheet = client.open_by_url("https://docs.google.com/spreadsheets/d/1Qt7zriA6f696jAeXv3XvzdJPmml8QuplGU9fU-3-SRs/edit").worksheet("HPPR Rankings")  # Adjust sheet name if needed

def get_players():
    try:
        if "players_cache" not in st.session_state:  # ✅ Only fetch once per session
            player_data = sheet.get_all_records()
            df = pd.DataFrame(player_data)

            if "elo" not in df.columns or df["elo"].isnull().all():
                df["elo"] = 1500  # Default Elo rating if missing

            df = df.copy()
            df["pos_rank"] = df.groupby("pos")["elo"].rank(method="min", ascending=False).astype(int)
            df = df.sort_values(by="elo", ascending=False)

            st.session_state["players_cache"] = df  # ✅ Store in session state
        return st.session_state["players_cache"]  # ✅ Use cached version
    except Exception as e:
        st.error(f"❌ Error fetching player data: {e}")
        return pd.DataFrame()

def get_player_value(player_name):
    """Fetches the Value column for a given player from the second Google Sheet."""
    try:
        data = value_sheet.get_all_records()  # Get all records from the sheet
        df = pd.DataFrame(data)

        # Ensure necessary columns exist
        if "Player Name" not in df.columns or "Value" not in df.columns:
            st.error("❌ Error: 'Player Name' or 'Value' column missing in Google Sheet.")
            return None

        # Find player and get their Value
        player_row = df[df["Player Name"].str.lower() == player_name.lower()]
        if not player_row.empty:
            return float(player_row["Value"].values[0])  # Convert value to float
        else:
            return None  # Player not found

    except Exception as e:
        st.error(f"❌ Error fetching player value: {e}")
        return None

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

def get_user_data(force_refresh=False):
    """Fetch user vote data from Google Sheets with optional cache refresh."""
    if force_refresh or "user_data_cache" not in st.session_state:  # ✅ Refresh if needed
        data = votes_sheet.get_all_records()

        if not data:
            st.session_state["user_data_cache"] = pd.DataFrame(columns=["username", "total_votes", "weekly_votes", "last_voted"])
        else:
            df = pd.DataFrame(data)
            df.columns = df.columns.str.lower()
            df["username"] = df["username"].str.lower()  # ✅ Normalize usernames to lowercase
            st.session_state["user_data_cache"] = df

    return st.session_state["user_data_cache"]  # ✅ Use cached data

def update_user_vote(username, count_vote=True):
    df = get_user_data()
    today = datetime.date.today().strftime("%Y-%m-%d")

    # ✅ Convert to lowercase to ensure case-insensitive lookup
    df["username"] = df["username"].str.lower()
    username_lower = username.lower()
    
    if df.empty or username_lower not in df["username"].values:
        votes_sheet.append_row([username, 1 if count_vote else 0, 1 if count_vote else 0, today])
        return


    row_idx = df[df["username"] == username].index[0] + 2
    values = votes_sheet.row_values(row_idx)

    total_votes = int(values[1]) if values[1].isdigit() else 0
    weekly_votes = int(values[2]) if values[2].isdigit() else 0
    last_voted = values[3]

    updates = []

    # ✅ Only update values if they have changed
    if datetime.datetime.today().weekday() == 0 and last_voted != today:
        updates.append({"range": f"R{row_idx}C3", "values": [[0]]})

    if count_vote:
        if total_votes + 1 != int(values[1]):  # ✅ Only update if different
            updates.append({"range": f"R{row_idx}C2", "values": [[total_votes + 1]]})
        if weekly_votes + 1 != int(values[2]):
            updates.append({"range": f"R{row_idx}C3", "values": [[weekly_votes + 1]]})

    if last_voted != today:
        updates.append({"range": f"R{row_idx}C4", "values": [[today]]})

    if updates:
        votes_sheet.batch_update(updates)  # ✅ Only send updates if needed
        st.session_state["user_data_cache"] = get_user_data(force_refresh=True)  # ✅ Refresh cached votes

# Elo Calculation (Moved Above Process_Vote)
def calculate_elo(winner_elo, loser_elo, k=24):
    expected_winner = 1 / (1 + 10 ** ((loser_elo - winner_elo) / 400))
    expected_loser = 1 / (1 + 10 ** ((winner_elo - loser_elo) / 400))
    new_winner_elo = winner_elo + k * (1 - expected_winner)
    new_loser_elo = loser_elo + k * (0 - expected_loser)
    return round(new_winner_elo), round(new_loser_elo)

def aggressive_weighted_selection(df, weight_col="elo", alpha=10):
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

def update_google_sheet(player1_name, player1_new_elo, player2_name, player2_new_elo):
    try:
        df = st.session_state["players_cache"]  # ✅ Use cached player data
        header_row = sheet.row_values(1)  # ✅ Get headers once

        elo_col_index = header_row.index("elo") + 1
        votes_col_index = header_row.index("Votes") + 1 if "Votes" in header_row else None

        # ✅ Fetch Elo instantly from memory
        player1_elo = get_player_elo(player1_name)
        player2_elo = get_player_elo(player2_name)

        # ✅ Get row indices from cache instead of API calls
        player1_row = df[df["name"].str.lower() == player1_name.lower()]
        player2_row = df[df["name"].str.lower() == player2_name.lower()]

        if player1_row.empty or player2_row.empty:
            return  # ✅ Prevent errors if players aren't found

        # ✅ Dynamically find player row to prevent incorrect updates if sorting changes
        all_values = sheet.get_all_values()  # ✅ Get all player names & rows
        player1_row_idx = next((i + 1 for i, row in enumerate(all_values) if row and row[0].strip().lower() == player1_name.lower()), None)
        player2_row_idx = next((i + 1 for i, row in enumerate(all_values) if row and row[0].strip().lower() == player2_name.lower()), None)
        
        if not player1_row_idx or not player2_row_idx:
            st.error(f"❌ Could not find player rows in Google Sheet for {player1_name} or {player2_name}.")
            return  # ✅ Prevent updating the wrong player


        updates = []

        # ✅ Only update Elo if it's changed
        if float(player1_new_elo) != float(player1_elo):
            updates.append({"range": f"R{player1_row_idx}C{elo_col_index}", "values": [[float(player1_new_elo)]]})

        if float(player2_new_elo) != float(player2_elo):
            updates.append({"range": f"R{player2_row_idx}C{elo_col_index}", "values": [[float(player2_new_elo)]]})

        if votes_col_index:
            # ✅ Read votes directly from Google Sheets instead of cache
            player1_votes = int(sheet.cell(player1_row_idx, votes_col_index).value or 0) + 1
            player2_votes = int(sheet.cell(player2_row_idx, votes_col_index).value or 0) + 1
        
            updates.append({"range": f"R{player1_row_idx}C{votes_col_index}", "values": [[player1_votes]]})
            updates.append({"range": f"R{player2_row_idx}C{votes_col_index}", "values": [[player2_votes]]})

        if updates:
            sheet.batch_update(updates)  # ✅ Only update if changes were detected

    except gspread.exceptions.APIError as e:
        st.error(f"❌ Google Sheets API Error: {e}")
    except Exception as e:
        st.error(f"❌ Unexpected error updating Google Sheet: {e}")

def process_vote(selected_player):
    with st.status("Submitting your pick and adjusting the rankings! ⏳", expanded=False) as status:
        if selected_player == player1["name"]:
            new_elo1, new_elo2 = calculate_elo(player1["elo"], player2["elo"])
        else:
            new_elo2, new_elo1 = calculate_elo(player2["elo"], player1["elo"])

        # ✅ Optimize by ensuring minimal API calls
        update_google_sheet(player1["name"], new_elo1, player2["name"], new_elo2)
        update_user_vote(st.session_state["username"], count_vote=True)

        # ✅ Store results instantly in session state (no extra API calls)
        st.session_state["updated_elo"] = {player1["name"]: new_elo1, player2["name"]: new_elo2}
        st.session_state["selected_player"] = selected_player

        # ✅ Instantly update status (without extra sleep)
        status.update(label="✅ Pick Submitted! Rankings Updated.", state="complete")

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
    st.markdown("## ⏳ Weekly Leaderboard (Resets Every Monday)")
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
