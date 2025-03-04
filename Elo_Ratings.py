import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import json
import pandas as pd
import random

creds_dict = st.secrets["gcp_service_account"]
scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
client = gspread.authorize(creds)  # ✅ Authorizing gspread
sheet = client.open("Community Elo Ratings").worksheet("Sheet1")  # Ensure correct sheet name

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

# ✅ Call `get_players()` AFTER `sheet` is initialized
players = get_players()

# Elo Calculation (Moved Above Process_Vote)
def calculate_elo(winner_elo, loser_elo, k=32):
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

# Streamlit UI
st.markdown("<h1 style='text-align: center;'>Who Would You Rather Draft?</h1>", unsafe_allow_html=True)

col1, col2 = st.columns(2)

def update_google_sheet(player_name, new_elo):
    try:
        cell = sheet.find(player_name.strip(), case_sensitive=False)
        header_row = sheet.row_values(1)  # Get column headers
        if "elo" in header_row:
            elo_col_index = header_row.index("elo") + 1  # Get column index dynamically
            sheet.update_cell(cell.row, elo_col_index, float(new_elo))  # Convert Elo to number
            # time.sleep(1)  # Ensure API update completes
        else:
            st.error("Elo column not found in Google Sheet")
    except gspread.exceptions.CellNotFound:
        st.error(f"Player {player_name} not found in Google Sheet")
    except Exception as e:
        st.error(f"Error updating Elo: {str(e)}")

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

