-- One row per NFL game. Grain: game_id.
--
-- A thin pass-through of stg_nflverse__schedules, deliberately. The staging model
-- already did all the typing and renaming this project needs (gameday -> DATE,
-- gametime -> TIME, 0/1 flags -> BOOLEAN), and a game is already exactly at this
-- dimension's grain in the source. There is no business logic to add between
-- "flattened, typed, renamed game record" and "the game dimension" — inventing a
-- transformation here just to have one would be the abstraction this project's
-- conventions warn against.

select * from {{ ref('stg_nflverse__schedules') }}
