"""Generate the benchmark: ~200 (question, gold SQL) pairs.

Hand-writing 200 unambiguous gold queries is slow and error-prone. Instead each
item comes from a *family* with a parameter pool, which gives us breadth cheaply
while keeping the gold SQL machine-authored and therefore actually correct.

Three rules the generator enforces, because a broken benchmark silently
inflates every number downstream:

  1. every gold SQL must execute against the real database (T2 traps included)
  2. question text must be unique across the set
  3. gold queries that return zero rows are tagged `trivial` so the runner can
     exclude them from the headline metric instead of crediting empty matches
"""

from __future__ import annotations

import json
import re
import sqlite3
import zlib
from pathlib import Path

from sqlglot import exp

from ..eval.scoring import execute
from ..safety import SafetyViolation, parse_one, referenced_tables

ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = ROOT / "data" / "learning_platform.db"
OUT = ROOT / "data" / "tasks.jsonl"

CITIES = ["Beijing", "Shanghai", "shenzhen", "Hangzhou", "Chengdu", "Guangzhou", "Wuhan", "Nanjing", "Suzhou", "Chongqing"]
CATS = ["data", "frontend", "backend", "ai", "design", "product", "devops"]
CHANNELS = ["web", "ios", "android", "mini_program"]
DEVICES = ["desktop", "mobile", "tablet"]
LEVELS = ["beginner", "intermediate", "advanced"]
YEARS = ["2025"]
MONTHS = [f"2025-{m:02d}" for m in range(1, 13)]

# Rewordings that must not change meaning. Used to stop the agent from pattern
# matching on a fixed surface form.
PARAPHRASE = [
    "{q}",
    "In the learning platform database, {q_lower}",
    "Using the platform tables, {q_lower}",
    "{q}",
    "Please answer with SQL: {q_lower}",
]


def q_variants(text: str) -> str:
    # zlib.crc32, not builtin hash(): the latter is salted per process by
    # PYTHONHASHSEED and would silently reshuffle every question on each build.
    tpl = PARAPHRASE[zlib.crc32(text.encode("utf-8")) % len(PARAPHRASE)]
    return tpl.format(q=text, q_lower=text[0].lower() + text[1:])


FAMILIES: list[tuple[str, str, list[tuple[str, str]]]] = []


def family(category: str, difficulty: str):
    def deco(fn):
        FAMILIES.append((category, difficulty, [(t[0], t[1]) for t in fn()]))
        return fn

    return deco


# ---------------------------------------------------------------- easy
@family("filter_projection", "easy")
def _f1():
    return [
        (q_variants(f"How many users are there whose city is {c}?"),
         f"SELECT COUNT(*) AS n FROM users WHERE LOWER(TRIM(city)) = LOWER('{c}')")
        for c in CITIES
    ] + [
        (q_variants(f"Return the id and email of all users in {c} whose account is not deleted."),
         f"SELECT id, email FROM users WHERE LOWER(TRIM(city)) = LOWER('{c}') AND is_deleted = 0")
        for c in CITIES
    ]


@family("aggregate_count", "easy")
def _f2():
    return [
        (q_variants(f"How many courses are in the {c} category?"),
         f"SELECT COUNT(*) AS n FROM courses WHERE category = '{c}'")
        for c in CATS
    ] + [
        (q_variants(f"How many orders belong to a course in the {c} category?"),
         f"SELECT COUNT(*) AS n FROM orders o JOIN courses c ON c.id = o.course_id WHERE c.category = '{c}'")
        for c in CATS
    ] + [
        (q_variants(f"How many published courses are at the {l} level?"),
         f"SELECT COUNT(*) AS n FROM courses WHERE status = 'published' AND LOWER(level) = LOWER('{l}')")
        for l in LEVELS
    ] + [
        (q_variants(f"How many orders have status paid, treating upper and lower case as equal?"),
         "SELECT COUNT(*) AS n FROM orders WHERE LOWER(status) = 'paid'"),
        (q_variants("How many enrollments have status completed, ignoring case?"),
         "SELECT COUNT(*) AS n FROM enrollments WHERE LOWER(status) = 'completed'"),
        (q_variants("How many courses are published?"),
         "SELECT COUNT(*) AS n FROM courses WHERE status = 'published'"),
        (q_variants("How many live classes are already over capacity (booked above capacity)?"),
         "SELECT COUNT(*) AS n FROM live_classes WHERE booked > capacity"),
        (q_variants("How many reviews are hidden?"),
         "SELECT COUNT(*) AS n FROM reviews WHERE hidden = 1"),
        (q_variants("How many lessons are of kind exercise?"),
         "SELECT COUNT(*) AS n FROM lessons WHERE kind = 'exercise'"),
    ]


@family("null_handling", "easy")
def _f3():
    return [
        (q_variants("How many users have no phone number recorded?"),
         "SELECT COUNT(*) AS n FROM users WHERE phone IS NULL"),
        (q_variants("How many quiz attempts have a missing score?"),
         "SELECT COUNT(*) AS n FROM quiz_attempts WHERE score IS NULL"),
        (q_variants("How many orders have a non-null refund amount?"),
         "SELECT COUNT(*) AS n FROM orders WHERE refund_amount IS NOT NULL"),
        (q_variants("How many progress rows have no watched_seconds value?"),
         "SELECT COUNT(*) AS n FROM progress WHERE watched_seconds IS NULL"),
        (q_variants("How many instructors have no rating recorded?"),
         "SELECT COUNT(*) AS n FROM instructors WHERE rating IS NULL"),
        (q_variants("How many users have no city stored at all (null, not merely blank)?"),
         "SELECT COUNT(*) AS n FROM users WHERE city IS NULL"),
        (q_variants("How many orders were placed without a coupon?"),
         "SELECT COUNT(*) AS n FROM orders WHERE coupon_id IS NULL"),
        (q_variants("How many enrollments have a null amount_paid?"),
         "SELECT COUNT(*) AS n FROM enrollments WHERE amount_paid IS NULL"),
        (q_variants("How many live classes have no instructor assigned?"),
         "SELECT COUNT(*) AS n FROM live_classes WHERE instructor_id IS NULL"),
        (q_variants("How many quiz attempts have neither score nor duration recorded?"),
         "SELECT COUNT(*) AS n FROM quiz_attempts WHERE score IS NULL AND duration_sec IS NULL"),
    ]


@family("ordering_limit", "easy")
def _f4():
    return [
        (q_variants("Which course has the highest price? Give its id, title and price."),
         "SELECT id, title, price FROM courses ORDER BY price DESC LIMIT 1"),
        (q_variants("List the 5 most expensive courses with id and price, highest first."),
         "SELECT id, price FROM courses ORDER BY price DESC LIMIT 5"),
        (q_variants("Which instructor has the highest rating? Give id and rating."),
         "SELECT id, rating FROM instructors WHERE rating IS NOT NULL ORDER BY rating DESC LIMIT 1"),
        (q_variants("Give the 3 longest lessons by duration, with id and duration_min."),
         "SELECT id, duration_min FROM lessons ORDER BY duration_min DESC LIMIT 3"),
        (q_variants("Which order has the largest refund amount? Give id and refund_amount."),
         "SELECT id, refund_amount FROM orders WHERE refund_amount IS NOT NULL ORDER BY refund_amount DESC LIMIT 1"),
        (q_variants("Give the 10 cheapest published courses, id and price, cheapest first."),
         "SELECT id, price FROM courses WHERE status = 'published' ORDER BY price ASC LIMIT 10"),
        (q_variants("Which live class has the largest overflow of booked over capacity? Give id and the overflow, only for classes that are over capacity."),
         "SELECT id, (booked - capacity) AS overflow FROM live_classes WHERE booked > capacity ORDER BY overflow DESC LIMIT 1"),
        (q_variants("Which 10 users have the most study sessions? Give user_id and the session count, highest first."),
         "SELECT user_id, COUNT(*) AS n FROM study_sessions GROUP BY user_id ORDER BY n DESC LIMIT 10"),
        (q_variants("Give the 10 longest review comments by character count, with id and length, longest first."),
         "SELECT id, LENGTH(comment) AS len FROM reviews WHERE comment IS NOT NULL ORDER BY len DESC LIMIT 10"),
        (q_variants("Which chapter has the most lessons? Give chapter_id and the count."),
         "SELECT chapter_id, COUNT(*) AS n FROM lessons GROUP BY chapter_id ORDER BY n DESC LIMIT 1"),
    ]


@family("distinct_count", "easy")
def _f5():
    out = []
    for c in CATS:
        out.append((q_variants(f"How many distinct users have any enrollment in the {c} category?"),
                    f"SELECT COUNT(DISTINCT e.user_id) AS n FROM enrollments e JOIN courses c ON c.id = e.course_id WHERE c.category = '{c}'"))
    out += [
        (q_variants("How many different cities appear in the users table, ignoring blanks and case?"),
         "SELECT COUNT(DISTINCT LOWER(TRIM(city))) AS n FROM users WHERE city IS NOT NULL AND TRIM(city) <> ''"),
        (q_variants("How many distinct coupon codes were used by at least one order?"),
         "SELECT COUNT(DISTINCT coupon_id) AS n FROM orders WHERE coupon_id IS NOT NULL"),
        # DISTINCT in the projection, not inside COUNT: dropping it changes the row
        # set, which is what makes duplicate fan-out a detectable failure
        (q_variants("List each distinct city value exactly as it is stored in the users table, without normalising case or spacing."),
         "SELECT DISTINCT city FROM users WHERE city IS NOT NULL"),
        (q_variants("List each distinct enrollment channel."),
         "SELECT DISTINCT channel FROM enrollments"),
        (q_variants("List each distinct course category."),
         "SELECT DISTINCT category FROM courses"),
        (q_variants("List each distinct device used for studying."),
         "SELECT DISTINCT device FROM study_sessions"),
        (q_variants("List each distinct lesson kind."),
         "SELECT DISTINCT kind FROM lessons"),
    ]
    return out


# ---------------------------------------------------------------- medium
@family("group_by", "medium")
def _f6():
    return [
        (q_variants(f"For each {g}, how many rows are in the {t} table? Give the group and the count."),
         f"SELECT {expr} AS grp, COUNT(*) AS n FROM {t} GROUP BY grp ORDER BY n DESC")
        for g, t, expr in [
            ("source", "users", "source"),
            ("channel", "enrollments", "channel"),
            ("device", "study_sessions", "device"),
            ("level", "courses", "level"),
            ("status", "orders", "status"),
            ("category", "courses", "category"),
            ("gender", "users", "gender"),
        ]
    ] + [
        (q_variants("For each city, how many non-deleted users live there? Give city and count."),
         "SELECT LOWER(TRIM(city)) AS city, COUNT(*) AS n FROM users WHERE is_deleted = 0 GROUP BY city ORDER BY n DESC"),
        (q_variants("For each course category, what is the average price? Give category and the average."),
         "SELECT category, AVG(price) AS avg_price FROM courses GROUP BY category ORDER BY avg_price DESC"),
        (q_variants("For each instructor, how many courses do they own? Give instructor_id and count."),
         "SELECT instructor_id, COUNT(*) AS n FROM courses GROUP BY instructor_id ORDER BY n DESC"),
    ]


@family("having", "medium")
def _f7():
    return [
        (q_variants(f"Which courses have more than {n} enrollments? Give course_id and the count."),
         f"SELECT course_id, COUNT(*) AS n FROM enrollments GROUP BY course_id HAVING COUNT(*) > {n} ORDER BY n DESC")
        for n in (30, 40, 50, 60, 80)
    ] + [
        (q_variants("Which users have placed more than 5 orders? Give user_id and order count."),
         "SELECT user_id, COUNT(*) AS n FROM orders GROUP BY user_id HAVING COUNT(*) > 5 ORDER BY n DESC"),
        (q_variants("Which quizzes have been attempted fewer than 30 times? Give quiz_id and attempt count."),
         "SELECT quiz_id, COUNT(*) AS n FROM quiz_attempts GROUP BY quiz_id HAVING COUNT(*) < 30 ORDER BY n"),
    ]


@family("join_two", "medium")
def _f8():
    return [
        (q_variants(f"How many paid orders belong to courses in the {c} category?"),
         f"SELECT COUNT(*) AS n FROM orders o JOIN courses c ON c.id = o.course_id "
         f"WHERE LOWER(o.status) = 'paid' AND c.category = '{c}'")
        for c in CATS
    ] + [
        (q_variants("For each course, show its title and its department name."),
         "SELECT co.title, d.name AS dept_name FROM courses co JOIN departments d ON d.id = co.dept_id"),
        (q_variants("Show course titles with the name of the instructor who owns them."),
         "SELECT co.title, i.name AS instructor_name FROM courses co JOIN instructors i ON i.id = co.instructor_id"),
        (q_variants("Which users received a certificate? Give distinct user name and course title."),
         "SELECT DISTINCT u.name, co.title FROM certificates ce JOIN users u ON u.id = ce.user_id JOIN courses co ON co.id = ce.course_id"),
    ]


@family("date_bucket", "medium")
def _f9():
    return [
        (q_variants(f"How many enrollments were made in {m}? Use the first 7 characters of enrolled_at."),
         f"SELECT COUNT(*) AS n FROM enrollments WHERE SUBSTR(enrolled_at, 1, 7) = '{m}'")
        for m in MONTHS
    ] + [
        (q_variants("How many orders were created in each month? Give the month and the count, ordered by month."),
         "SELECT SUBSTR(created_at, 1, 7) AS month, COUNT(*) AS n FROM orders GROUP BY month ORDER BY month"),
        (q_variants("How many certificates were issued in each calendar month? Give month and count."),
         "SELECT SUBSTR(issued_at, 1, 7) AS month, COUNT(*) AS n FROM certificates GROUP BY month ORDER BY month"),
    ]


@family("conditional_agg", "medium")
def _f10():
    return [
        (q_variants("How many review rows give a rating of 4 or higher, and how many give 2 or lower? Give both counts in one row."),
         "SELECT SUM(CASE WHEN rating >= 4 THEN 1 ELSE 0 END) AS high, "
         "SUM(CASE WHEN rating <= 2 THEN 1 ELSE 0 END) AS low FROM reviews"),
        (q_variants("Per course category, how many enrollments are completed and how many are cancelled? Give category plus both counts."),
         "SELECT c.category, SUM(CASE WHEN LOWER(e.status) = 'completed' THEN 1 ELSE 0 END) AS completed, "
         "SUM(CASE WHEN LOWER(e.status) = 'cancelled' THEN 1 ELSE 0 END) AS cancelled "
         "FROM enrollments e JOIN courses c ON c.id = e.course_id GROUP BY c.category"),
        (q_variants("What fraction of all progress rows are completed? Give the rate as a single number."),
         "SELECT SUM(CASE WHEN completed = 1 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS rate FROM progress"),
        (q_variants("What fraction of orders are refunded? Give the rate as a single number."),
         "SELECT SUM(CASE WHEN LOWER(status) = 'refunded' THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS rate FROM orders"),
    ]


# ---------------------------------------------------------------- hard
@family("subquery", "hard")
def _f11():
    return [
        (q_variants("Which non-deleted users have never placed an order? Give their ids."),
         "SELECT u.id FROM users u WHERE u.is_deleted = 0 AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.user_id = u.id)"),
        (q_variants("List courses whose price is above the overall average price, with id and price."),
         "SELECT id, price FROM courses WHERE price > (SELECT AVG(price) FROM courses)"),
        (q_variants("Which instructors own no course at all? Give id and name."),
         "SELECT id, name FROM instructors WHERE id NOT IN (SELECT instructor_id FROM courses WHERE instructor_id IS NOT NULL)"),
        (q_variants("Which courses have no enrollment? Give their ids."),
         "SELECT id FROM courses WHERE id NOT IN (SELECT course_id FROM enrollments)"),
        (q_variants("Give the ids of users who have an enrollment in every category 'ai' course."),
         "SELECT e.user_id FROM enrollments e JOIN courses c ON c.id = e.course_id WHERE c.category = 'ai' "
         "GROUP BY e.user_id HAVING COUNT(DISTINCT e.course_id) = (SELECT COUNT(*) FROM courses WHERE category = 'ai')"),
    ]


@family("join_three", "hard")
def _f12():
    out = []
    for c in CATS:
        out.append((q_variants(f"For the {c} category, how many distinct non-deleted users have a paid order?"),
                    f"SELECT COUNT(DISTINCT o.user_id) AS n FROM orders o "
                    f"JOIN courses c ON c.id = o.course_id JOIN users u ON u.id = o.user_id "
                    f"WHERE LOWER(o.status) = 'paid' AND c.category = '{c}' AND u.is_deleted = 0"))
    out += [
        (q_variants("For each department, how many distinct users have enrolled in one of its courses? Give department name and the count."),
         "SELECT d.name AS dept_name, COUNT(DISTINCT e.user_id) AS n FROM departments d "
         "JOIN courses c ON c.dept_id = d.id JOIN enrollments e ON e.course_id = c.id GROUP BY d.name ORDER BY n DESC"),
        (q_variants("For each instructor, how many completed enrollments do their courses have? Give instructor name and count."),
         "SELECT i.name AS instructor_name, COUNT(*) AS n FROM instructors i JOIN courses c ON c.instructor_id = i.id "
         "JOIN enrollments e ON e.course_id = c.id WHERE LOWER(e.status) = 'completed' GROUP BY i.name ORDER BY n DESC"),
        (q_variants("Which 5 courses have the most non-hidden positive reviews (rating >= 4)? Give course id and review count."),
         "SELECT c.id, COUNT(*) AS n FROM courses c JOIN enrollments e ON e.course_id = c.id "
         "JOIN reviews r ON r.enrollment_id = e.id AND r.hidden = 0 WHERE r.rating >= 4 "
         "GROUP BY c.id ORDER BY n DESC LIMIT 5"),
    ]
    return out


@family("window", "hard")
def _f13():
    return [
        (q_variants("Rank courses by enrollment count, highest first. Give course_id, the count and the rank."),
         "SELECT course_id, COUNT(*) AS n, RANK() OVER (ORDER BY COUNT(*) DESC) AS rk "
         "FROM enrollments GROUP BY course_id"),
        (q_variants("For each order, give user_id, created_at, amount and a running total of amount for that user ordered by created_at."),
         "SELECT user_id, created_at, amount, SUM(amount) OVER (PARTITION BY user_id ORDER BY created_at) AS running_total FROM orders"),
        (q_variants("For each user, how many orders have they placed, and what is that count relative to the average per user? Give user_id, count and the overall average in every row."),
         "SELECT user_id, COUNT(*) AS n, AVG(COUNT(*)) OVER () AS avg_per_user FROM orders GROUP BY user_id"),
        (q_variants("For each lesson, give its id, duration_min and the total duration of all lessons in the same chapter. Return only the first 100 rows ordered by id."),
         "SELECT id, duration_min, SUM(duration_min) OVER (PARTITION BY chapter_id) AS chapter_total FROM lessons ORDER BY id LIMIT 100"),
    ]


@family("topk_per_group", "hard")
def _f14():
    return [
        (q_variants("For each course category, which course has the most enrollments? Give category, course_id and the count."),
         "WITH ranked AS (SELECT c.category, e.course_id, COUNT(*) AS n, "
         "ROW_NUMBER() OVER (PARTITION BY c.category ORDER BY COUNT(*) DESC) AS rn "
         "FROM enrollments e JOIN courses c ON c.id = e.course_id GROUP BY c.category, e.course_id) "
         "SELECT category, course_id, n FROM ranked WHERE rn = 1 ORDER BY category"),
        (q_variants("For each device type, which user spent the most study seconds in total? Give device, user_id and seconds."),
         "WITH ranked AS (SELECT device, user_id, SUM(seconds) AS s, "
         "ROW_NUMBER() OVER (PARTITION BY device ORDER BY SUM(seconds) DESC) AS rn "
         "FROM study_sessions GROUP BY device, user_id) SELECT device, user_id, s FROM ranked WHERE rn = 1 ORDER BY device"),
        (q_variants("For each month, which course had the most new enrollments? Give month, course_id and count, limited to months where the top course has at least 5 enrollments."),
         "WITH ranked AS (SELECT SUBSTR(enrolled_at,1,7) AS month, course_id, COUNT(*) AS n, "
         "ROW_NUMBER() OVER (PARTITION BY SUBSTR(enrolled_at,1,7) ORDER BY COUNT(*) DESC) AS rn "
         "FROM enrollments GROUP BY month, course_id) SELECT month, course_id, n FROM ranked WHERE rn = 1 AND n >= 5 ORDER BY month"),
    ]


@family("traps", "hard")
def _f15():
    """Questions whose obvious answer is wrong; each one isolates a single trap."""
    return [
        (q_variants("How many real (not soft-deleted) users have enrolled in course 7? Count each user once even if they enrolled twice."),
         "SELECT COUNT(DISTINCT e.user_id) AS n FROM enrollments e JOIN users u ON u.id = e.user_id "
         "WHERE e.course_id = 7 AND u.is_deleted = 0"),
        (q_variants("How many enrollments exist for course 7 (count rows, not people)?"),
         "SELECT COUNT(*) AS n FROM enrollments WHERE course_id = 7"),
        (q_variants("How many distinct users have a progress row with completed = 1 in the 'ai' category?"),
         "SELECT COUNT(DISTINCT p.id) AS n FROM progress p JOIN enrollments e ON e.id = p.enrollment_id "
         "JOIN courses c ON c.id = e.course_id WHERE p.completed = 1 AND c.category = 'ai'"),
        (q_variants("What is the average watched_seconds across progress rows, where missing values should not be counted?"),
         "SELECT AVG(watched_seconds) AS avg_watched FROM progress WHERE watched_seconds IS NOT NULL"),
    ]


@family("metric_by_group", "medium")
def _f16():
    specs = [
        ("course category", "total amount actually paid on enrollments",
         "SELECT c.category, SUM(e.amount_paid) AS total_paid FROM enrollments e JOIN courses c ON c.id = e.course_id GROUP BY c.category ORDER BY total_paid DESC"),
        ("order status", "total order amount",
         "SELECT status, SUM(amount) AS total FROM orders GROUP BY status ORDER BY total DESC"),
        ("channel", "average amount paid per enrollment",
         "SELECT channel, AVG(amount_paid) AS avg_paid FROM enrollments GROUP BY channel ORDER BY avg_paid DESC"),
        ("device", "total study seconds",
         "SELECT device, SUM(seconds) AS total_seconds FROM study_sessions GROUP BY device ORDER BY total_seconds DESC"),
        ("lesson kind", "count of lessons and their summed duration",
         "SELECT kind, COUNT(*) AS n, SUM(duration_min) AS total_min FROM lessons GROUP BY kind ORDER BY n DESC"),
        ("course level", "average credit",
         "SELECT level, AVG(credit) AS avg_credit FROM courses GROUP BY level ORDER BY avg_credit DESC"),
        ("department", "count of courses",
         "SELECT d.name AS dept_name, COUNT(c.id) AS n FROM departments d LEFT JOIN courses c ON c.dept_id = d.id GROUP BY d.name ORDER BY n DESC"),
        ("instructor title", "average instructor rating",
         "SELECT title, AVG(rating) AS avg_rating FROM instructors GROUP BY title ORDER BY avg_rating DESC"),
        ("month", "count of study sessions",
         "SELECT SUBSTR(started_at,1,7) AS month, COUNT(*) AS n FROM study_sessions GROUP BY month ORDER BY month"),
        ("coupon", "count of orders that used each coupon",
         "SELECT coupon_id, COUNT(*) AS n FROM orders WHERE coupon_id IS NOT NULL GROUP BY coupon_id ORDER BY n DESC"),
        ("course", "average review rating, non-hidden reviews only",
         "SELECT e.course_id, AVG(r.rating) AS avg_rating FROM reviews r JOIN enrollments e ON e.id = r.enrollment_id WHERE r.hidden = 0 GROUP BY e.course_id ORDER BY avg_rating DESC"),
        ("quiz", "average attempt score",
         "SELECT quiz_id, AVG(score) AS avg_score FROM quiz_attempts GROUP BY quiz_id ORDER BY avg_score DESC"),
    ]
    return [(q_variants(f"For each {g}, what is the {m}? Give the group and the value."), sql) for g, m, sql in specs]


@family("category_slice", "hard")
def _f17():
    out = []
    for c in CATS:
        out.append((q_variants(f"For the {c} category, what is the average rating given in non-hidden reviews?"),
                    f"SELECT AVG(r.rating) AS avg_rating FROM reviews r JOIN enrollments e ON e.id = r.enrollment_id "
                    f"JOIN courses co ON co.id = e.course_id WHERE r.hidden = 0 AND co.category = '{c}'"))
        out.append((q_variants(f"For the {c} category, how many lessons exist across all its courses?"),
                    f"SELECT COUNT(l.id) AS n FROM lessons l JOIN chapters ch ON ch.id = l.chapter_id "
                    f"JOIN courses co ON co.id = ch.course_id WHERE co.category = '{c}'"))
        out.append((q_variants(f"For the {c} category, how many certificates were issued?"),
                    f"SELECT COUNT(*) AS n FROM certificates ce JOIN courses co ON co.id = ce.course_id WHERE co.category = '{c}'"))
        out.append((q_variants(f"For the {c} category, what is the pass rate of quiz attempts, where passing means a score at or above the quiz passing_score? Missing scores do not count."),
                    f"SELECT SUM(CASE WHEN a.score >= q.passing_score THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS pass_rate "
                    f"FROM quiz_attempts a JOIN quizzes q ON q.id = a.quiz_id JOIN courses co ON co.id = q.course_id "
                    f"WHERE a.score IS NOT NULL AND co.category = '{c}'"))
        out.append((q_variants(f"For the {c} category, how many distinct non-deleted users have an active or completed enrollment? Count each user once."),
                    f"SELECT COUNT(DISTINCT e.user_id) AS n FROM enrollments e JOIN courses co ON co.id = e.course_id "
                    f"JOIN users u ON u.id = e.user_id AND u.is_deleted = 0 "
                    f"WHERE co.category = '{c}' AND LOWER(e.status) IN ('active','completed')"))
    return out


@family("text_match", "medium")
def _f18():
    return [
        (q_variants("How many review comments contain the word 'course' anywhere in the text?"),
         "SELECT COUNT(*) AS n FROM reviews WHERE comment LIKE '%course%'"),
        (q_variants("How many messages contain the word 'extension'?"),
         "SELECT COUNT(*) AS n FROM messages WHERE body LIKE '%extension%'"),
        (q_variants("How many courses have a title that begins with 'Intro to'?"),
         "SELECT COUNT(*) AS n FROM courses WHERE title LIKE 'Intro to %'"),
        (q_variants("List the ids of review rows whose comment contains the word 'instructions'. Give id only."),
         "SELECT id FROM reviews WHERE comment LIKE '%instructions%'"),
        (q_variants("How many review comments contain 'instructions' and are not hidden?"),
         "SELECT COUNT(*) AS n FROM reviews WHERE comment LIKE '%instructions%' AND hidden = 0"),
        (q_variants("How many lessons have a title starting with 'Lesson 3'?"),
         "SELECT COUNT(*) AS n FROM lessons WHERE title LIKE 'Lesson 3%'"),
    ]


@family("arithmetic", "hard")
def _f19():
    return [
        (q_variants("For the 20 courses with the most paid orders, give course_id, paid order count and net revenue (paid amount minus refunds on those paid orders)."),
         "SELECT o.course_id, COUNT(*) AS paid_orders, SUM(o.amount - COALESCE(o.refund_amount, 0)) AS net_revenue "
         "FROM orders o WHERE LOWER(o.status) = 'paid' GROUP BY o.course_id ORDER BY paid_orders DESC LIMIT 20"),
        (q_variants("Which live classes still have free seats? Give id and the number of seats left, most seats left first."),
         "SELECT id, (capacity - booked) AS seats_left FROM live_classes WHERE booked < capacity ORDER BY seats_left DESC"),
        (q_variants("For each user, give their total study time in hours (seconds divided by 3600), for users with more than 10 sessions."),
         "SELECT user_id, SUM(seconds) / 3600.0 AS hours FROM study_sessions GROUP BY user_id HAVING COUNT(*) > 10 ORDER BY hours DESC"),
        (q_variants("For enrollments with at least 5 progress rows, give enrollment_id and the share of those rows that are completed. Limit to the 25 highest shares."),
         "SELECT enrollment_id, SUM(CASE WHEN completed = 1 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS rate "
         "FROM progress GROUP BY enrollment_id HAVING COUNT(*) >= 5 ORDER BY rate DESC LIMIT 25"),
        (q_variants("Give each course's total lesson duration in hours, using hours = duration_min summed and divided by 60, for courses with at least 5 lessons."),
         "SELECT ch.course_id, SUM(l.duration_min) / 60.0 AS hours FROM lessons l JOIN chapters ch ON ch.id = l.chapter_id "
         "GROUP BY ch.course_id HAVING COUNT(l.id) >= 5 ORDER BY hours DESC"),
        (q_variants("What is the difference between the number of paid orders and the number of refunded orders? Give a single number."),
         "SELECT SUM(CASE WHEN LOWER(status) = 'paid' THEN 1 ELSE 0 END) - "
         "SUM(CASE WHEN LOWER(status) = 'refunded' THEN 1 ELSE 0 END) AS diff FROM orders"),
        (q_variants("For each course, give the ratio of cancelled enrollments to all enrollments, only for courses with at least 20 enrollments. Show course_id and the ratio."),
         "SELECT course_id, SUM(CASE WHEN LOWER(status) = 'cancelled' THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS cancel_rate "
         "FROM enrollments GROUP BY course_id HAVING COUNT(*) >= 20 ORDER BY cancel_rate DESC"),
    ]


def tie_risk(conn: sqlite3.Connection, sql: str) -> bool | None:
    """True/False for a LIMIT top-k whose cut lands inside equal sort keys.

    Tied top-k has no single correct answer, so those items must not be scored on
    sequence. None means "not statically checkable" (the ORDER BY key could not be
    resolved to an output column), which the build report surfaces rather than
    quietly treating as safe.
    """
    try:
        tree = parse_one(sql)
    except SafetyViolation:
        return None
    limit, order = tree.args.get("limit"), tree.args.get("order")
    if order is None:
        # no outer ORDER BY: nothing to tie on, and sequence must never be enforced
        return None
    outs = [(e.alias_or_name or "").lower() for e in tree.expressions]
    keys: list[int] = []
    for o in order.expressions:
        e = o.this
        if isinstance(e, exp.Literal) and e.is_int:
            keys.append(int(e.this) - 1)
        else:
            name = (e.alias_or_name or e.name or "").lower()
            if name in outs:
                keys.append(outs.index(name))
            else:
                return None
    if not keys:
        return None
    if limit is None:
        # full listing: any repeated sort key means the sequence is not unique
        rows = execute(conn, sql)
        if not rows.ok:
            return None
        k = [tuple(r[j] for j in keys) for r in rows.rows]
        return any(a == b for a, b in zip(k, k[1:]))
    probe = tree.copy()
    probe.set("limit", None)
    probe.set("offset", None)
    full = execute(conn, probe.sql(dialect="sqlite"))
    if not full.ok:
        return None
    k = int(limit.expression.this)
    if len(full.rows) <= k:
        return False
    return tuple(full.rows[k - 1][j] for j in keys) == tuple(full.rows[k][j] for j in keys)


def demands_order(question: str) -> bool:
    return _ORDER_DEMANDS.search(question) is not None


_ORDER_DEMANDS = re.compile(
    r"\b(ordered by|sorted by|in descending order|in ascending order|from most|"
    r"rank|top \d+|which \d+|highest first|lowest first|cheapest first|longest first|"
    r"largest first|most seats left first|most first|earliest first|latest first)\b",
    re.I,
)


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit("no database - run: python -m sqlagent.data.build_db")
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)

    tasks: list[dict] = []
    seen_questions: set[str] = set()
    broken: list[str] = []
    unchecked: list[str] = []
    tied: list[str] = []

    for category, difficulty, items in FAMILIES:
        for idx, (question, sql) in enumerate(items, 1):
            key = question.strip().lower()
            if key in seen_questions:
                continue
            seen_questions.add(key)
            ex = execute(conn, sql)
            if not ex.ok:
                broken.append(f"[{category}#{idx}] {ex.error[:120]}\n    {sql}")
                continue
            try:
                tables = sorted(referenced_tables(parse_one(sql)))
            except SafetyViolation:
                tables = []
            risk = tie_risk(conn, sql)
            if risk is True:
                tied.append(f"{category}-{idx:03d}")
            if risk is None and demands_order(question):
                unchecked.append(f"{category}-{idx:03d}")
            tasks.append({
                "id": f"{category}-{idx:03d}",
                "question": question,
                "gold_sql": sql,
                "category": category,
                "difficulty": difficulty,
                "gold_tables": tables,
                "gold_row_count": len(ex.rows),
                "trivial": len(ex.rows) == 0,
                # enforced only where the question asked for an order AND the
                # requested top-k is provably tie-free
                # enforced only when the question asked for an order, the gold
                # really sorts, and that sort is provably tie-free
                "require_order": demands_order(question) and risk is False,
            })

    out = OUT
    out.write_text("\n".join(json.dumps(t, ensure_ascii=False) for t in tasks) + "\n", encoding="utf-8")

    print(f"wrote {len(tasks)} tasks -> {out.relative_to(ROOT)}")
    by_cat: dict[str, int] = {}
    by_diff: dict[str, int] = {}
    for t in tasks:
        by_cat[t["category"]] = by_cat.get(t["category"], 0) + 1
        by_diff[t["difficulty"]] = by_diff.get(t["difficulty"], 0) + 1
    print("by category:", json.dumps(by_cat, indent=None))
    print("by difficulty:", by_diff)
    print("trivial (0-row) tasks:", sum(1 for t in tasks if t["trivial"]))
    print("tasks enforcing row order:", sum(1 for t in tasks if t["require_order"]))
    print(f"duplicate sort keys ({len(tied)}) -> scored as sets, not sequences: {tied}")
    if unchecked:
        print(f"note: {len(unchecked)} questions imply an order the grader cannot enforce "
              f"(gold has no resolvable outer ORDER BY): {unchecked}")
    if broken:
        print(f"\n!! {len(broken)} gold queries FAILED to execute and were dropped:")
        for b in broken:
            print("  " + b)
    conn.close()


if __name__ == "__main__":
    main()
