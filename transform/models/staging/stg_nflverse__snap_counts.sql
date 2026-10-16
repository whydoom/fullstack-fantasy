-- One row per player per game: snaps played on offence, defence and special teams
-- and the share of his team's snaps. Grain: (game_id, pfr_player_id). Rename only.
-- nflverse sources these from Pro Football Reference, so players carry a PFR id,
-- not a GSIS id; the player ID crosswalk's pfr_id bridges the two.

select
    game_id,
    pfr_game_id,
    season,
    week,
    game_type                   as season_type,
    pfr_player_id,
    player                      as player_name,
    position,
    team                        as team_abbr,
    opponent                    as opponent_abbr,
    offense_snaps,
    offense_pct                 as offense_snap_share,
    defense_snaps,
    defense_pct                 as defense_snap_share,
    st_snaps                    as special_teams_snaps,
    st_pct                      as special_teams_snap_share

from {{ source('raw_nflverse', 'snap_counts') }}
