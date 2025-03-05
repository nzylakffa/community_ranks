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
client = gspread.authorize(creds)  # ✅ Authorizing gspread
sheet = client.open("Community Elo Ratings").worksheet("Sheet1")  # Ensure correct sheet name
# ✅ New sheet reference for tracking user votes
votes_sheet = client.open("Community Elo Ratings").worksheet("UserVotes")  # Ensure "UserVotes" exists


# ✅ Move this below `sheet` initialization
def get_players():
    try:
        player_data = sheet.get_all_records()
        df = pd.DataFrame(player_data)

        if "elo" not in df.columns or df["elo"].isnull().all():
            df["elo"] = 1500  # Default Elo rating if missing

        df["pos_rank"] = df.groupby("pos")["elo"].rank(method="min", ascending=False).astype(int)
        df = df.sort_values(by="elo", ascending=False)
        return df
    except Exception as e:
        st.error(f"❌ Error fetching player data: {e}")
        return pd.DataFrame()

def get_user_data():
    data = votes_sheet.get_all_records()
    
    # ✅ Handle empty Google Sheet (only headers, no data)
    if not data:
        return pd.DataFrame(columns=["username", "total_votes", "weekly_votes", "last_voted"])  # ✅ Create empty DataFrame
    
    df = pd.DataFrame(data)
    df.columns = df.columns.str.lower()  # ✅ Ensure lowercase column names for consistency

    return df  # ✅ Cleaned up, no debug output

def update_user_vote(username):
    df = get_user_data()
    today = datetime.date.today().strftime("%Y-%m-%d")

    # ✅ If DataFrame is empty or username doesn't exist, add the user
    if df.empty or username not in df["username"].values:
        st.warning(f"New user added: {username}")
        votes_sheet.append_row([username, 1, 1, today])  # ✅ Initialize with 1 vote
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

    # ✅ Update vote counts
    votes_sheet.update_cell(row_idx, 2, total_votes + 1)  # Increment total_votes
    votes_sheet.update_cell(row_idx, 3, weekly_votes + 1)  # Increment weekly_votes
    votes_sheet.update_cell(row_idx, 4, today)  # Update last_voted date


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
    max_elo = df[weight_col].max()
    min_elo = df[weight_col].min()
    
    # Normalize Elo scores to avoid extreme weighting
    df["normalized_elo"] = (df[weight_col] - min_elo) / (max_elo - min_elo)
    
    # Exponentiate with alpha for stronger weighting on higher Elo players
    df["weight"] = df["normalized_elo"] ** alpha
    
    # Normalize weights to sum to 1 (softmax-like effect)
    df["weight"] = df["weight"] / df["weight"].sum()
    
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

# ✅ Username Input
st.markdown("### Enter Your Username to Track Your Rank:")
username = st.text_input("Username", value=st.session_state.get("username", ""), max_chars=15)

if username:
    st.session_state["username"] = username
    update_user_vote(username)  # ✅ Track votes in Google Sheets

# Streamlit UI
st.markdown("<h1 style='text-align: center;'>Who Would You Rather Draft?</h1>", unsafe_allow_html=True)

col1, col2 = st.columns(2)

def update_google_sheet(player_name, new_elo):
    try:
        cell = sheet.find(player_name.strip(), case_sensitive=False)
        header_row = sheet.row_values(1)  # Get column headers

        # ✅ Update Elo column dynamically
        if "elo" in header_row:
            elo_col_index = header_row.index("elo") + 1  # Convert to 1-based index
            sheet.update_cell(cell.row, elo_col_index, float(new_elo))  # Convert Elo to number

        # ✅ Update Count column dynamically
        if "Votes" in header_row:
            count_col_index = header_row.index("Votes") + 1  # Convert to 1-based index
            current_count = sheet.cell(cell.row, count_col_index).value  # Get current count
            new_count = int(current_count) + 1 if current_count and current_count.isdigit() else 1
            sheet.update_cell(cell.row, count_col_index, new_count)  # Increment count

    except gspread.exceptions.CellNotFound:
        st.error(f"❌ Player {player_name} not found in Google Sheet")
    except Exception as e:
        st.error(f"❌ Error updating Elo & Count: {str(e)}")

def process_vote(selected_player):
    if selected_player == player1["name"]:
        new_elo1, new_elo2 = calculate_elo(player1["elo"], player2["elo"])
    else:
        new_elo2, new_elo1 = calculate_elo(player2["elo"], player1["elo"])

    # Update Google Sheet
    update_google_sheet(player1["name"], new_elo1)
    update_google_sheet(player2["name"], new_elo2)

    # Store new Elo values
    st.session_state["updated_elo"] = {player1["name"]: new_elo1, player2["name"]: new_elo2}
    st.session_state["selected_player"] = selected_player

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

    # ✅ Load leaderboard data
    df = get_user_data()
    
    # ✅ Display All-Time Leaderboard
    st.markdown("## 🏆 All-Time Leaderboard (Total Votes)")
    df_all_time = df.sort_values(by="total_votes", ascending=False).head(5)  # Top 5 users
    st.table(df_all_time)
    
    # ✅ Display Weekly Leaderboard
    st.markdown("## ⏳ Weekly Leaderboard (Resets Every Monday)")
    df_weekly = df.sort_values(by="weekly_votes", ascending=False).head(5)  # Top 5 users
    st.table(df_weekly)

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
        st.experimental_rerun()

    st.markdown("</div>", unsafe_allow_html=True)
