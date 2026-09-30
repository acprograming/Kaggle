"""V6: the tape backbone, with every wasted command repurposed in place.

The tape replays a trajectory recorded in a *different* game, so a large share
of its commands land on tiles that no longer hold what the recording expected.
Measured over three games: 43.2% of unit-commands are movement, and a further
12.2% (925 per game) are outright no-ops -- watering bare earth, caring for an
absent animal, collecting fertilizer from nothing. Together with the 247 PASS
commands per game, about 1,170 actions a game do nothing at all.

V6 detects those commands and substitutes useful work, but only work the unit
can do from the tile it is already standing on. That restriction matters: an
earlier version that let idle units walk up to two tiles scored 0.88 of the
tape, because moving a unit desynchronises the tape's positional schedule and
the route correction costs more than the extra work earns. Standing still is
free, so this is strictly additive.

Movement commands and any command that actually does something are never
touched, which keeps the backbone's behaviour and its cash flow intact -- also
load-bearing, since modifying the tape's market head was measured to break
its purchase schedule badly.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("_tape_v6", _ROOT / "baseline" / "tape_agent.py")
_tape = importlib.util.module_from_spec(_spec)
sys.modules["_tape_v6"] = _tape
_spec.loader.exec_module(_tape)

BOARD = 10
MOVES = ("NORTH", "SOUTH", "EAST", "WEST")
CROP_FIRST = {"WHEAT": 2, "CARROT": 2, "MELON": 10, "TOMATO": 8, "STRAWBERRY": 10}
CROP_MAXDAY = {"WHEAT": 4, "CARROT": 3, "MELON": 12, "TOMATO": 8, "STRAWBERRY": 10}
CROP_ONGOING = {"WHEAT": False, "CARROT": False, "MELON": False, "TOMATO": True, "STRAWBERRY": True}
STRUCT_OF = {"GOOSE": "COOP", "COW": "PASTURE", "SHEEP": "PASTURE"}
MAXHELD = {"GOOSE": 4, "COW": 6, "SHEEP": 6}
SEED_COST = {"WHEAT": 10, "CARROT": 20, "TOMATO": 50, "STRAWBERRY": 100, "MELON": 80}
ANIMAL_COST = {"GOOSE": 300, "COW": 400, "SHEEP": 500}

REPLACE_NOOPS = os.environ.get("V6_NOOPS", "1") == "1"
# Which substitutions are permitted. PLANT and PLACE draw on the shared seed
# and animal pools that the tape buys to exact targets, so taking from them
# turns the tape's own scheduled commands into no-ops. Excluded by default.
ALLOW = set(x for x in os.environ.get("V6_ALLOW", "WATER,HARVEST,CARE,DIG").split(",") if x)

# Narrow re-admissions of the resource-consuming substitutions, each limited to
# a case where the resource cannot be wanted more by the tape itself.
#   FEED_URGENT: only an animal that escapes tonight. A lost animal is
#     unrecoverable, so that wheat cannot have a better use.
#   PLACE_LATE: only after the tape has stopped placing animals. ~13 animals a
#     game were measured sitting in the shed at the final turn.
#   PLANT_EXCESS: only seeds held beyond the tape's own target for that crop,
#     which are surplus by construction.
FEED_URGENT = os.environ.get("V6_FEED_URGENT", "0") == "1"
PLACE_LATE_DAY = int(os.environ.get("V6_PLACE_LATE", "-1"))
PLANT_EXCESS = os.environ.get("V6_PLANT_EXCESS", "0") == "1"
# Override which recorded trajectory the backbone follows. The shipped agent
# keys on the first store to unlock, which assumes the tape recorded under a
# store also *plays* that store best. TAPE_FORCE lets that be tested.
TAPE_FORCE = os.environ.get("TAPE_FORCE", "").strip()
# Drop animal purchases from this day on. An animal bought near the end cannot
# repay its cost: a cow is 400 and yields once every two days from day 8 of its
# life. The tape was measured finishing games with ~2 cows and ~3 geese still
# in the shed, never placed. Unlike deferring a purchase (which desynchronised
# the tape's pickup/place steps and cost 0.10x), dropping a late one removes
# spending the schedule has no remaining use for.
NO_ANIMALS_FROM_DAY = int(os.environ.get("V6_NO_ANIMALS_FROM", "-1"))
# Normally a movement command is never touched, since the backbone's schedule
# is positional. This permits one exception: a unit standing on a plant that
# will become a weed tonight, or an animal that will escape, may water or feed
# instead of stepping away. That costs one step of schedule slip but saves a
# tile (or an animal) for the rest of the season.
RESCUE_ON_MOVE = os.environ.get("V6_RESCUE", "0") == "1"
# A rescue may only pre-empt a scheduled step this late in the day. Without an
# hour gate the rule fires constantly and is ruinous (9.6% against 42.9%): a
# freshly planted seed starts at consecutive_unwatered = 1, so "dies tonight"
# is true of every new planting from the moment it goes in, and the backbone
# was going to water it later the same day anyway.
RESCUE_FROM_HOUR = int(os.environ.get("V6_RESCUE_HOUR", "20"))

# --- Melon rush -----------------------------------------------------------
# Melon is the one product the town barely touches: shops demand none of it and
# the town centre takes one a day, so its market inventory is almost purely
# player-driven. Its glut curve is quadratic with T=300, which means the first
# ~150 melons sold market-wide are worth something and the rest are not. It is
# therefore a race, not a market.
#
# Measured over 40 matches, both sides currently dump melon on day 10 (we sell
# 30.7 units that day, the opponent 34.2) and both realise ~$201. But our
# melons reach their capped yield of 6 at age 8, because fertilizing brings the
# cap forward two days. Harvesting on the day the cap is reached, rather than
# at the recorded time, puts our stock on the market a day or two ahead of
# theirs. Selling all 73 of ours before their 70 is worth about +7,900 of
# margin against the +760 we get from arriving together: we would realise ~$233
# a unit and leave them ~$130.
#
# Harvest lands in the unit's inventory and only reaches the shed at the
# end-of-day drop, so a day-8 harvest becomes a day-9 sale. That is still
# ahead of their day 10.
MELON_RUSH = os.environ.get("V6_MELON_RUSH", "0") == "1"
MELON_MIN_YIELD = int(os.environ.get("V6_MELON_YIELD", "6"))
MELON_RUSH_FROM_AGE = int(os.environ.get("V6_MELON_AGE", "8"))
MELON_RUSH_OVERRIDE_MOVE = os.environ.get("V6_MELON_MOVE", "0") == "1"

# Melon expansion. Winning the race by arriving first is worth ~+7,900 of
# margin; winning it by volume is worth far more. Selling 150 melons before the
# opponent's 70 leaves them about $1 a unit, a swing near +25,000. 150 units is
# 25 tiles at 6 units each, and melon is labour-cheap (watering ages 6-10 plus
# one harvest) so the cost is seed money and land, not work.
#
# The constraint is timing: a melon must be in the ground by about day 1 to cap
# by day 9, which is exactly when cash is tightest. So extra seeds are bought
# only out of cash the backbone's own schedule does not need, and planted only
# on tiles that are empty anyway, using only the surplus above its seed target.
MELON_EXPAND = int(os.environ.get("V6_MELON_EXPAND", "0"))
MELON_EXPAND_UNTIL_DAY = int(os.environ.get("V6_MELON_EXPAND_DAY", "1"))
MELON_CASH_FLOOR = float(os.environ.get("V6_MELON_CASH", "600"))
# Funding the rush out of "spare" cash bankrupted the opening (score 0): the
# spare calculation only saw the current turn's orders, while the backbone needs
# the whole 3,000 across the opening. The honest way to pay for melons is to
# displace something, and animals are the candidate. Two cows cost 800 and
# return roughly 6,000 of milk over the season; 10 melons cost the same and, if
# they reach market before the opponent's, are worth about 8,500 to us and
# strip ~13,000 from them. That trade is worth making.
MELON_DISPLACE_ANIMALS = int(os.environ.get("V6_MELON_DISPLACE", "0"))
_SUBST_STATS = {"noops_seen": 0, "noops_used": 0, "pass_seen": 0, "pass_used": 0}


def _is_noop(cmd, tile, bag, seeds, day, shed):
    """True only when the command provably changes nothing.

    Deliberately conservative: anything uncertain is treated as effective and
    left alone, so the backbone is never disturbed.
    """
    op = cmd[0]
    if op in MOVES or op == "PASS":
        return False
    isd = isinstance(tile, dict)
    kind = tile.get("kind") if isd else None
    if op == "WATER":
        return kind != "PLANT" or bool(tile.get("watered_today"))
    if op == "HARVEST":
        if not isd or tile.get("yield_units", 0) <= 0:
            return True
        if kind == "PLANT" and day - tile.get("planted_day", day) < CROP_FIRST[tile["crop"]]:
            return True
        return False
    if op == "FEED":
        if not (isd and tile.get("animal")):
            return True
        return bool(tile.get("fed_today")) or bag.get("WHEAT", 0) <= 0
    if op == "CARE":
        if not (isd and tile.get("animal")):
            return True
        return bool(tile.get("cared_today"))
    if op == "FERTILIZE":
        return kind != "PLANT" or bag.get("FERTILIZER", 0) <= 0
    if op == "PLANT":
        crop = cmd[1] if len(cmd) > 1 else None
        return tile is not None or seeds.get(crop, 0) <= 0
    if op == "DIG":
        return tile is None
    if op in ("BUILD_COOP", "BUILD_PASTURE"):
        return tile is not None
    if op == "PLACE":
        item = cmd[1] if len(cmd) > 1 else None
        if item in STRUCT_OF:
            if not (isd and kind == STRUCT_OF[item] and not tile.get("animal")):
                return True
            return bag.get(item, 0) <= 0
        return False
    if op == "COLLECT_FERTILIZER":
        if not (isd and tile.get("animal")):
            return True
        return not tile.get("fertilizer_available")
    if op == "PICKUP":
        return shed.get(cmd[1] if len(cmd) > 1 else None, 0) <= 0
    if op == "DROP":
        return sum(bag.values()) == 0
    return False


def _in_window(tile, day):
    crop = tile.get("crop")
    if crop is None or CROP_ONGOING.get(crop):
        return False
    age = day - tile.get("planted_day", day)
    return ((CROP_MAXDAY[crop] + 1) // 2) <= age <= CROP_MAXDAY[crop]


def _harvestable(tile, day):
    crop = tile.get("crop")
    if crop is None or tile.get("yield_units", 0) <= 0:
        return False
    age = day - tile.get("planted_day", day)
    if age < CROP_FIRST[crop]:
        return False
    return True if CROP_ONGOING[crop] else age >= CROP_MAXDAY[crop]


def _in_place_work(tile, bag, day, seeds, surplus=None, melon_surplus=None):
    """Best action available without leaving this tile, or None."""
    if tile is None:
        if melon_surplus and melon_surplus[0] > 0:
            return 80.0, ["PLANT", "MELON"]
        if surplus:
            for crop in ("CARROT", "WHEAT", "MELON", "TOMATO", "STRAWBERRY"):
                if surplus.get(crop, 0) > 0:
                    return 30.0, ["PLANT", crop]
        return None
    if not isinstance(tile, dict):
        return None
    kind = tile.get("kind")
    if kind == "WEED":
        return 20.0, ["DIG"]
    if kind == "PLANT":
        if not tile.get("watered_today"):
            if tile.get("consecutive_unwatered", 0) >= 1:
                return 130.0, ["WATER"]      # otherwise it becomes a weed tonight
            if _in_window(tile, day):
                return 70.0, ["WATER"]       # a watered day is a whole extra unit
        if _harvestable(tile, day):
            return 60.0, ["HARVEST"]
        if (not CROP_ONGOING.get(tile.get("crop"))
                and tile.get("fertilized_until_day", -1) < day
                and _in_window(tile, day) and bag.get("FERTILIZER", 0) > 0):
            return 34.0, ["FERTILIZE"]
        return None
    if tile.get("animal"):
        animal = tile["animal"]
        held = tile.get("yield_units", 0)
        if (not tile.get("fed_today") and bag.get("WHEAT", 0) > 0
                and (not FEED_URGENT or tile.get("consecutive_unfed", 0) >= 1)):
            return 140.0, ["FEED"]           # unfed twice and the animal is gone
        if held >= MAXHELD[animal]:
            return 100.0, ["HARVEST"]        # at the cap, further yield is lost
        if not tile.get("cared_today") and tile.get("fed_today"):
            # CARE banks a bonus paid on the next yield: about double a goose's
            # output, triple a cow's, for a single action.
            return 55.0, ["CARE"]
        if held > 0:
            return 45.0, ["HARVEST"]
        if not tile.get("cared_today"):
            return 40.0, ["CARE"]
        if tile.get("fertilizer_available"):
            return 18.0, ["COLLECT_FERTILIZER"]
        return None
    if kind in ("COOP", "PASTURE") and not tile.get("animal"):
        if PLACE_LATE_DAY >= 0 and day >= PLACE_LATE_DAY:
            for animal, structure in STRUCT_OF.items():
                if structure == kind and bag.get(animal, 0) > 0:
                    return 110.0, ["PLACE", animal, 1]
    return None


_inner_market = _tape._market


def _market_filtered(obs, action, row, config):
    orders = _inner_market(obs, action, row, config)
    if NO_ANIMALS_FROM_DAY >= 0 and obs["day"] >= NO_ANIMALS_FROM_DAY:
        orders = [o for o in orders if not (o and o[0] == "BUY_ANIMAL")]
    if MELON_DISPLACE_ANIMALS > 0 and obs["day"] <= MELON_DISPLACE_ANIMALS:
        seat = int(obs.get("player", 0))
        farm = obs["farms"][seat]
        freed = 0.0
        kept = []
        for order in orders:
            if order and order[0] == "BUY_ANIMAL":
                freed += ANIMAL_COST.get(order[1], 0) * int(order[2])
            else:
                kept.append(order)
        if freed > 0:
            orders = kept
            private = obs["private"]
            held = int(private.get("seeds", {}).get("MELON", 0))
            planted = sum(1 for r in farm["tiles"] for t in r
                          if isinstance(t, dict) and t.get("crop") == "MELON")
            room = sum(1 for r in farm["tiles"] for t in r if t is None)
            want = min(int(freed // SEED_COST["MELON"]),
                       max(0, MELON_EXPAND - planted - held),
                       room + 2)
            if want > 0 and len(orders) < 10:
                orders.append(["BUY_SEED", "MELON", want])
    elif MELON_EXPAND > 0 and obs["day"] <= MELON_EXPAND_UNTIL_DAY and len(orders) < 10:
        seat = int(obs.get("player", 0))
        farm = obs["farms"][seat]
        private = obs["private"]
        held = int(private.get("seeds", {}).get("MELON", 0))
        planted = sum(1 for r in farm["tiles"] for t in r
                      if isinstance(t, dict) and t.get("crop") == "MELON")
        want = MELON_EXPAND - planted - held
        if want > 0:
            # Spend only what the backbone's own orders have already left over.
            spare = float(farm["money"])
            for order in orders:
                if not order:
                    continue
                if order[0] == "BUY_SEED":
                    spare -= SEED_COST.get(order[1], 0) * int(order[2])
                elif order[0] == "BUY_ANIMAL":
                    spare -= ANIMAL_COST.get(order[1], 0) * int(order[2])
                elif order[0] == "BUY_PRODUCT":
                    spare -= obs["market"]["prices"].get(order[1], 0) * int(order[2])
                elif order[0] == "BUY_LAND":
                    spare -= 4000
                elif order[0] == "SELL":
                    spare += obs["market"]["prices"].get(order[1], 0) * int(order[2])
            budget = max(0.0, spare - MELON_CASH_FLOOR)
            qty = min(want, int(budget // SEED_COST["MELON"]))
            if qty > 0:
                orders.append(["BUY_SEED", "MELON", qty])
    return orders


_tape._market = _market_filtered


def _current_row(obs, seat):
    """The tape row the backbone is following this step, if it can be read."""
    try:
        step = int(obs.get("step", 24 * obs["day"] + obs["hour"]))
        if step < 72:
            return _tape._TAPES["opening"][min(step, 71)]
        key = (_tape._STATE.get(seat) or {}).get("key")
        if key is None or key not in _tape._TAPES:
            return None
        return _tape._TAPES[key][min(step, 718)]
    except Exception:
        return None


def agent(obs, configuration=None):
    seat = int(obs.get("player", 0))
    if TAPE_FORCE:
        st = _tape._STATE.get(seat)
        if st is not None:
            st["key"] = TAPE_FORCE
    action = _tape.agent(obs, configuration)
    if TAPE_FORCE:
        st = _tape._STATE.get(seat)
        if st is not None:
            st["key"] = TAPE_FORCE
    farm = obs["farms"][seat]
    day = obs["day"]
    private = obs["private"]
    bags = private.get("inventories", [])
    seeds = dict(private.get("seeds", {}))
    shed = dict(private.get("shed", {}))
    positions = [tuple(farm["farmer"])] + [tuple(p) for p in farm["hands"]]
    units = [action.get("farmer") or ["PASS"]] + list(action.get("hands") or [])

    # Melon seeds held beyond the backbone's own target for this step are ours
    # to plant: it never asked for them, so planting them steals nothing.
    melon_surplus = None
    if MELON_EXPAND > 0:
        row = _current_row(obs, seat)
        target = 0
        if row is not None:
            try:
                target = int(row[3][list(_tape.CROPS).index("MELON")])
            except (IndexError, TypeError, ValueError):
                target = 0
        melon_surplus = [max(0, seeds.get("MELON", 0) - target)]

    surplus = None
    if PLANT_EXCESS:
        surplus = dict(seeds)
        row = _current_row(obs, seat)
        if row is not None:
            for ci, crop in enumerate(_tape.CROPS):
                try:
                    surplus[crop] = max(0, seeds.get(crop, 0) - int(row[3][ci]))
                except (IndexError, TypeError, ValueError):
                    surplus[crop] = 0

    for i, cmd in enumerate(units):
        if i >= len(positions) or not cmd:
            continue
        x, y = positions[i]
        tile = farm["tiles"][y][x]
        if tile == "LOCKED":
            continue
        bag = bags[i] if i < len(bags) else {}

        # Melon rush: a capped melon is worth more in the shed today than on
        # the vine tomorrow, because the melon market is a race. This is rare
        # enough (a dozen tiles, briefly) that it may pre-empt a scheduled step
        # without the cascade that sank the general rescue rule.
        if (MELON_RUSH and isinstance(tile, dict) and tile.get("crop") == "MELON"
                and tile.get("yield_units", 0) >= MELON_MIN_YIELD
                and day - tile.get("planted_day", day) >= MELON_RUSH_FROM_AGE
                and cmd[0] != "HARVEST"):
            if cmd[0] not in MOVES or MELON_RUSH_OVERRIDE_MOVE:
                units[i] = ["HARVEST"]
                _SUBST_STATS["melon_rush"] = _SUBST_STATS.get("melon_rush", 0) + 1
                continue

        if cmd[0] in MOVES and not RESCUE_ON_MOVE:
            continue
        if cmd[0] in MOVES:
            # Only an outright rescue, late enough that the backbone will not
            # reach the tile itself, justifies abandoning a scheduled step.
            rescue = None
            if isinstance(tile, dict) and obs["hour"] >= RESCUE_FROM_HOUR:
                if (tile.get("kind") == "PLANT" and not tile.get("watered_today")
                        and tile.get("consecutive_unwatered", 0) >= 1):
                    rescue = ["WATER"]
                elif (tile.get("animal") and not tile.get("fed_today")
                      and tile.get("consecutive_unfed", 0) >= 1 and bag.get("WHEAT", 0) > 0
                      and "FEED" in ALLOW):
                    rescue = ["FEED"]
            if rescue is not None and rescue[0] in ALLOW:
                units[i] = rescue
                _SUBST_STATS["rescues"] = _SUBST_STATS.get("rescues", 0) + 1
            continue
        idle = cmd[0] == "PASS"
        if idle:
            _SUBST_STATS["pass_seen"] += 1
        else:
            if not REPLACE_NOOPS:
                continue
            if not _is_noop(cmd, tile, bag, seeds, day, shed):
                continue
            _SUBST_STATS["noops_seen"] += 1
        found = _in_place_work(tile, bag, day, seeds, surplus, melon_surplus)
        if not found:
            continue
        _value, replacement = found
        melon_plant = replacement[0] == "PLANT" and replacement[1] == "MELON" and MELON_EXPAND > 0
        if replacement[0] not in ALLOW and not melon_plant:
            continue
        if melon_plant:
            if not melon_surplus or melon_surplus[0] <= 0:
                continue
            melon_surplus[0] -= 1
            seeds["MELON"] = max(0, seeds.get("MELON", 0) - 1)
            units[i] = replacement
            _SUBST_STATS["melon_planted"] = _SUBST_STATS.get("melon_planted", 0) + 1
            continue
        if replacement[0] == "PLANT":
            crop = replacement[1]
            if not surplus or surplus.get(crop, 0) <= 0:
                continue
            surplus[crop] -= 1
            seeds[crop] = max(0, seeds.get(crop, 0) - 1)
        units[i] = replacement
        _SUBST_STATS["pass_used" if idle else "noops_used"] += 1

    action["farmer"] = units[0] if units else ["PASS"]
    action["hands"] = units[1:]
    return action


agent.telemetry = _SUBST_STATS
