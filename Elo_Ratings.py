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


# ✅ Move this below `sheet` initialization
def get_players():
    try:
        player_data = sheet.get_all_records()
        df = pd.DataFrame(player_data)

        if "elo" not in df.columns or df["elo"].isnull().all():
            df["elo"] = 1500  # Default Elo rating if missing

        df = df.copy()  # ✅ Ensure modifications are made on a fresh copy
        df["pos_rank"] = df.groupby("pos")["elo"].rank(method="min", ascending=False).astype(int)
        df = df.sort_values(by="elo", ascending=False)
        return df
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


def get_user_data():
    data = votes_sheet.get_all_records()
    
    # ✅ Handle empty Google Sheet (only headers, no data)
    if not data:
        return pd.DataFrame(columns=["username", "total_votes", "weekly_votes", "last_voted"])  # ✅ Create empty DataFrame
    
    df = pd.DataFrame(data)
    df.columns = df.columns.str.lower()  # ✅ Ensure lowercase column names for consistency

    return df  # ✅ Cleaned up, no debug output

def update_user_vote(username, count_vote=True):
    df = get_user_data()
    today = datetime.date.today().strftime("%Y-%m-%d")

    # ✅ If DataFrame is empty or username doesn't exist, add the user
    if df.empty or username not in df["username"].values:
        votes_sheet.append_row([username, 1 if count_vote else 0, 1 if count_vote else 0, today])  # ✅ Initialize with 1 vote only if count_vote=True
        return  # ✅ Exit after adding a new user

    # ✅ If user exists, update their vote counts
    row_idx = df[df["username"] == username].index[0] + 2  # Adjust for Google Sheets index

    # ✅ Retrieve current vote counts, handling empty values
    total_votes = votes_sheet.cell(row_idx, 2).value  # Column 2 = total_votes
    weekly_votes = votes_sheet.cell(row_idx, 3).value  # Column 3 = weekly_votes
    last_voted = votes_sheet.cell(row_idx, 4).value  # Column 4 = last_voted (date)

    total_votes = int(total_votes) if total_votes and total_votes.isdigit() else 0  # ✅ Handle empty values
    weekly_votes = int(weekly_votes) if weekly_votes and weekly_votes.isdigit() else 0  # ✅ Handle empty values

    # ✅ Reset weekly votes if it's Monday and last vote was before today
    if datetime.datetime.today().weekday() == 0 and last_voted != today:
        votes_sheet.update_cell(row_idx, 3, 0)  # ✅ Reset weekly_votes

    # ✅ Only count votes once per selection
    if count_vote:
        votes_sheet.update_cell(row_idx, 2, total_votes + 1)  # Increment total_votes
        votes_sheet.update_cell(row_idx, 3, weekly_votes + 1)  # Increment weekly_votes
    
    votes_sheet.update_cell(row_idx, 4, today)  # ✅ Update last_voted date


# ✅ Call `get_players()` AFTER `sheet` is initialized
players = get_players()

# Elo Calculation (Moved Above Process_Vote)
def calculate_elo(winner_elo, loser_elo, k=24):
    expected_winner = 1 / (1 + 10 ** ((loser_elo - winner_elo) / 400))
    expected_loser = 1 / (1 + 10 ** ((winner_elo - loser_elo) / 400))
    new_winner_elo = winner_elo + k * (1 - expected_winner)
    new_loser_elo = loser_elo + k * (0 - expected_loser)
    return round(new_winner_elo), round(new_loser_elo)

# # Fetch players and pick two close in Elo with aggressive top weighting
# players = get_players()

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

# Initialize session state variables
if "player1" not in st.session_state or "player2" not in st.session_state:
    st.session_state.player1 = aggressive_weighted_selection(players)
    st.session_state.player2_candidates = players[
        (players["elo"] > st.session_state.player1["elo"] - 50) & (players["elo"] < st.session_state.player1["elo"] + 50)
    ]
    st.session_state.player2 = aggressive_weighted_selection(st.session_state.player2_candidates) if not st.session_state.player2_candidates.empty else aggressive_weighted_selection(players)

# Ensure `initial_elo` is always initialized
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

# ✅ Get player values from the second sheet
player1_value = get_player_value(player1["name"])
player2_value = get_player_value(player2["name"])

# ✅ Determine Nick's Pick (Highest Value)
if player1_value is not None and player2_value is not None:
    nicks_pick = player1["name"] if player1_value > player2_value else player2["name"]
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

def update_google_sheet(player1_name, player1_new_elo, player2_name, player2_new_elo):
    try:
        # ✅ Read Google Sheet only ONCE at the start
        all_values = sheet.get_all_values()
        header_row = all_values[0]  # Get column headers
        
        # ✅ Find column indexes dynamically
        elo_col_index = header_row.index("elo") + 1  # Convert to 1-based index
        votes_col_index = header_row.index("Votes") + 1 if "Votes" in header_row else None
        
        # ✅ Find player row positions only ONCE
        player1_row = next((i + 1 for i, row in enumerate(all_values) if row and row[0].strip().lower() == player1_name.lower()), None)
        player2_row = next((i + 1 for i, row in enumerate(all_values) if row and row[0].strip().lower() == player2_name.lower()), None)

        if not player1_row or not player2_row:
            st.error("❌ One or both players not found in Google Sheet")
            return

        # ✅ Prepare batch updates
        updates = [
            {"range": f"R{player1_row}C{elo_col_index}", "values": [[float(player1_new_elo)]]},
            {"range": f"R{player2_row}C{elo_col_index}", "values": [[float(player2_new_elo)]]}
        ]

        if votes_col_index:
            player1_votes = int(all_values[player1_row - 1][votes_col_index - 1] or 0) + 1
            player2_votes = int(all_values[player2_row - 1][votes_col_index - 1] or 0) + 1
            updates.extend([
                {"range": f"R{player1_row}C{votes_col_index}", "values": [[player1_votes]]},
                {"range": f"R{player2_row}C{votes_col_index}", "values": [[player2_votes]]}
            ])

        # ✅ Send a SINGLE batch update to reduce API calls
        sheet.batch_update(updates)

    except gspread.exceptions.APIError as e:
        st.error(f"❌ Google Sheets API Error: {e}")
    except Exception as e:
        st.error(f"❌ Unexpected error updating Google Sheet: {e}")

def process_vote(selected_player):
    # ✅ Show Status Message While Processing
    with st.status("Submitting your pick and adjusting the rankings! ⏳", expanded=False) as status:        
        if selected_player == player1["name"]:
            new_elo1, new_elo2 = calculate_elo(player1["elo"], player2["elo"])
        else:
            new_elo2, new_elo1 = calculate_elo(player2["elo"], player1["elo"])

        # ✅ Update Google Sheet (Ensuring Only One API Call)
        update_google_sheet(player1["name"], new_elo1, player2["name"], new_elo2)
        update_user_vote(st.session_state["username"], count_vote=True)

        # ✅ Store new Elo values in session state
        st.session_state["updated_elo"] = {player1["name"]: new_elo1, player2["name"]: new_elo2}
        st.session_state["selected_player"] = selected_player

        # ✅ Update Status to Completed
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

        if st.button("Draft", key=player["name"], use_container_width=True):
            process_vote(player["name"])

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
    # ✅ Display Nick's Pick Below Elo Ratings
    st.markdown(f"<p style='font-size:18px;'><b>Nick Would Have Picked:</b> {nicks_pick}</p>", unsafe_allow_html=True)


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
        # Select new players
        st.session_state["player1"] = aggressive_weighted_selection(players)
        st.session_state["player2_candidates"] = players[
            (players["elo"] > st.session_state["player1"]["elo"] - 50) & (players["elo"] < st.session_state["player1"]["elo"] + 50)
        ]
        st.session_state["player2"] = aggressive_weighted_selection(st.session_state["player2_candidates"]) if not st.session_state["player2_candidates"].empty else aggressive_weighted_selection(players)

        # Reset Elo tracking for new players
        st.session_state["initial_elo"] = {
            st.session_state["player1"]["name"]: st.session_state["player1"]["elo"],
            st.session_state["player2"]["name"]: st.session_state["player2"]["elo"]
        }

        # Reset selected player & updated Elo
        st.session_state["selected_player"] = None
        st.session_state["updated_elo"] = {}

        # Rerun Streamlit app to show new matchup
        st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)
