from datetime import datetime, timezone
from typing import List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.models import (
    SkillNode, SkillSubtopic, Achievement, UserAchievement, DailyQuest,
    TrackingLog, ChapterProgress, User, StudyPlan
)

# Hardcoded achievements config
DEFAULT_ACHIEVEMENTS = [
    {
        "id": "deep_work_demon",
        "name": "Deep Work Demon",
        "description": "Log 4 continuous hours of productive desktop time without distractions.",
        "icon": "Zap",
        "color": "#eab308",
        "xp_reward": 100,
        "criteria_type": "productive_hours",
        "criteria_value": 4
    },
    {
        "id": "consistency_king",
        "name": "Consistency King",
        "description": "Complete at least one study plan chapter 7 days in a row.",
        "icon": "Coffee",
        "color": "#3b82f6",
        "xp_reward": 150,
        "criteria_type": "streak_days",
        "criteria_value": 7
    },
    {
        "id": "polyglot",
        "name": "Polyglot",
        "description": "Master nodes in 3 completely different categories.",
        "icon": "Globe",
        "color": "#10b981",
        "xp_reward": 200,
        "criteria_type": "mastered_skills",
        "criteria_value": 3
    }
]

# Legacy JS config translated to Python for DB seeding
SKILL_TREE_SEED = [
    {
        "id": 'html-css', "name": 'HTML & CSS', "description": 'The foundation of the web — structure and styling.',
        "lucideIcon": 'Code', "color": '#e34c26', "tier": 1, "prerequisites": [], "connections": ['javascript'],
        "subtopics": [
            {"name": 'HTML Basics', "keywords": ['html basics', 'html fundamentals', 'html tags']},
            {"name": 'CSS Styling', "keywords": ['css styling', 'css basics', 'css fundamentals']},
            {"name": 'Responsive Design', "keywords": ['responsive', 'media queries', 'flexbox', 'css grid']},
            {"name": 'Semantic HTML', "keywords": ['semantic html', 'accessibility', 'web standards']},
        ]
    },
    {
        "id": 'mathematics', "name": 'Mathematics', "description": 'Algebra, calculus, discrete math, and logic.',
        "lucideIcon": 'Calculator', "color": '#6366f1', "tier": 1, "prerequisites": [], "connections": ['python', 'data-structures'],
        "subtopics": [
            {"name": 'Linear Algebra', "keywords": ['linear algebra', 'matrices', 'vectors']},
            {"name": 'Calculus', "keywords": ['calculus', 'derivatives', 'integrals']},
            {"name": 'Discrete Math', "keywords": ['discrete math', 'combinatorics', 'graph theory']},
            {"name": 'Probability & Statistics', "keywords": ['probability', 'statistics', 'stat ']},
        ]
    },
    {
        "id": 'c-lang', "name": 'C Language', "description": 'Low-level programming — pointers, memory, and systems.',
        "lucideIcon": 'Cpu', "color": '#555555', "tier": 1, "prerequisites": [], "connections": ['java', 'data-structures'],
        "subtopics": [
            {"name": 'C Basics', "keywords": ['c programming', 'c language', 'c basics']},
            {"name": 'Pointers & Memory', "keywords": ['pointers', 'memory management', 'malloc']},
            {"name": 'File I/O', "keywords": ['file handling in c', 'c file']},
        ]
    },
    {
        "id": 'javascript', "name": 'JavaScript', "description": 'The language of the web — dynamic, versatile, everywhere.',
        "lucideIcon": 'Braces', "color": '#f7df1e', "tier": 2, "prerequisites": ['html-css'], "connections": ['react', 'nodejs', 'typescript'],
        "subtopics": [
            {"name": 'JS Fundamentals', "keywords": ['javascript fundamentals', 'javascript basics', 'js basics']},
            {"name": 'DOM Manipulation', "keywords": ['dom manipulation', 'dom scripting']},
            {"name": 'Async & Promises', "keywords": ['async', 'promises', 'callbacks', 'event loop']},
            {"name": 'ES6+ Features', "keywords": ['es6', 'ecmascript', 'modern javascript', 'arrow functions']},
        ]
    },
    {
        "id": 'python', "name": 'Python', "description": 'Elegant, readable, powerful — from scripting to AI.',
        "lucideIcon": 'Terminal', "color": '#3776ab', "tier": 2, "prerequisites": [], "connections": ['django', 'flask', 'machine-learning'],
        "subtopics": [
            {"name": 'Python Basics', "keywords": ['python basics', 'python fundamentals', 'learn python']},
            {"name": 'OOP in Python', "keywords": ['python oop', 'object oriented python', 'classes in python']},
            {"name": 'Python Libraries', "keywords": ['numpy', 'pandas', 'matplotlib', 'python libraries']},
            {"name": 'File Handling & APIs', "keywords": ['python file', 'python api', 'requests library']},
        ]
    },
    {
        "id": 'java', "name": 'Java', "description": 'Enterprise-grade, object-oriented, battle-tested.',
        "lucideIcon": 'Coffee', "color": '#007396', "tier": 2, "prerequisites": [], "connections": ['springboot', 'data-structures'],
        "subtopics": [
            {"name": 'Java Basics', "keywords": ['java basics', 'java fundamentals', 'core java']},
            {"name": 'OOP in Java', "keywords": ['java oop', 'inheritance java', 'polymorphism java']},
            {"name": 'Collections Framework', "keywords": ['java collections', 'arraylist', 'hashmap java']},
            {"name": 'Multithreading', "keywords": ['java multithreading', 'concurrency java', 'threads java']},
        ]
    },
    {
        "id": 'react', "name": 'React', "description": 'Build dynamic UIs with component-based architecture.',
        "lucideIcon": 'Atom', "color": '#61dafb', "tier": 3, "prerequisites": ['javascript'], "connections": ['nextjs'],
        "subtopics": [
            {"name": 'React Fundamentals', "keywords": ['react fundamentals', 'react basics', 'learn react']},
            {"name": 'Hooks & State', "keywords": ['react hooks', 'usestate', 'useeffect', 'react state']},
            {"name": 'React Router', "keywords": ['react router', 'routing react']},
            {"name": 'Context & Redux', "keywords": ['react context', 'redux', 'state management']},
        ]
    },
    {
        "id": 'nodejs', "name": 'Node.js', "description": 'Server-side JavaScript — APIs, microservices, and more.',
        "lucideIcon": 'Server', "color": '#339933', "tier": 3, "prerequisites": ['javascript'], "connections": ['nextjs', 'databases'],
        "subtopics": [
            {"name": 'Node Basics', "keywords": ['node basics', 'nodejs basics', 'node.js fundamentals']},
            {"name": 'Express.js', "keywords": ['express.js', 'expressjs', 'express framework']},
            {"name": 'REST APIs', "keywords": ['rest api', 'api development', 'restful']},
            {"name": 'Authentication', "keywords": ['authentication node', 'jwt', 'passport.js', 'oauth']},
        ]
    },
    {
        "id": 'typescript', "name": 'TypeScript', "description": 'Type-safe JavaScript for large-scale applications.',
        "lucideIcon": 'FileType', "color": '#3178c6', "tier": 3, "prerequisites": ['javascript'], "connections": ['nextjs'],
        "subtopics": [
            {"name": 'TS Basics', "keywords": ['typescript basics', 'typescript fundamentals']},
            {"name": 'Types & Interfaces', "keywords": ['typescript types', 'interfaces typescript', 'generics typescript']},
            {"name": 'TS with React', "keywords": ['typescript react', 'react typescript']},
        ]
    },
    {
        "id": 'springboot', "name": 'Spring Boot', "description": 'Enterprise Java framework for production-grade APIs.',
        "lucideIcon": 'Leaf', "color": '#6db33f', "tier": 3, "prerequisites": ['java'], "connections": ['databases', 'devops'],
        "subtopics": [
            {"name": 'Spring Basics', "keywords": ['spring basics', 'spring boot basics', 'spring fundamentals', 'mastering spring boot', 'spring boot']},
            {"name": 'Spring MVC', "keywords": ['spring mvc', 'spring web', 'spring controller']},
            {"name": 'Spring Data JPA', "keywords": ['spring data', 'jpa', 'spring hibernate']},
            {"name": 'Spring Security', "keywords": ['spring security', 'spring auth']},
        ]
    },
    {
        "id": 'django', "name": 'Django', "description": 'High-level Python web framework — batteries included.',
        "lucideIcon": 'Globe', "color": '#092e20', "tier": 3, "prerequisites": ['python'], "connections": ['databases', 'devops'],
        "subtopics": [
            {"name": 'Django Basics', "keywords": ['django basics', 'django fundamentals', 'learn django']},
            {"name": 'Django Models', "keywords": ['django models', 'django orm', 'django database']},
            {"name": 'Django REST', "keywords": ['django rest', 'drf', 'django api']},
        ]
    },
    {
        "id": 'flask', "name": 'Flask', "description": 'Lightweight Python micro-framework for web apps.',
        "lucideIcon": 'Beaker', "color": '#888888', "tier": 3, "prerequisites": ['python'], "connections": ['databases'],
        "subtopics": [
            {"name": 'Flask Basics', "keywords": ['flask basics', 'flask fundamentals', 'learn flask']},
            {"name": 'Flask REST APIs', "keywords": ['flask api', 'flask rest', 'flask route']},
        ]
    },
    {
        "id": 'data-structures', "name": 'DSA', "description": 'Arrays, trees, graphs, hash maps — the building blocks.',
        "lucideIcon": 'Network', "color": '#f97316', "tier": 3, "prerequisites": [], "connections": ['algorithms'],
        "subtopics": [
            {"name": 'Arrays & Strings', "keywords": ['arrays', 'strings', 'two pointer', 'sliding window']},
            {"name": 'Linked Lists', "keywords": ['linked list', 'doubly linked', 'circular linked']},
            {"name": 'Stacks & Queues', "keywords": ['stack', 'queue', 'deque']},
            {"name": 'Trees & BST', "keywords": ['binary tree', 'binary search tree', 'bst', 'tree traversal']},
            {"name": 'Graphs', "keywords": ['graph', 'bfs', 'dfs', 'adjacency']},
            {"name": 'Hash Maps & Sets', "keywords": ['hash map', 'hash table', 'hash set', 'hashing']},
            {"name": 'Heaps', "keywords": ['heap', 'priority queue', 'min heap', 'max heap']},
        ]
    },
    {
        "id": 'nextjs', "name": 'Next.js', "description": 'Full-stack React framework with SSR and API routes.',
        "lucideIcon": 'Layers', "color": '#ffffff', "tier": 4, "prerequisites": ['react', 'nodejs'], "connections": ['devops'],
        "subtopics": [
            {"name": 'Next.js Basics', "keywords": ['next.js basics', 'nextjs basics', 'learn nextjs']},
            {"name": 'SSR & SSG', "keywords": ['server side rendering', 'static generation', 'ssr', 'ssg']},
            {"name": 'API Routes', "keywords": ['nextjs api', 'api routes next']},
        ]
    },
    {
        "id": 'databases', "name": 'Databases', "description": 'SQL, NoSQL, Redis — persist and query data efficiently.',
        "lucideIcon": 'Database', "color": '#336791', "tier": 4, "prerequisites": [], "connections": ['system-design'],
        "subtopics": [
            {"name": 'SQL Fundamentals', "keywords": ['sql basics', 'sql fundamentals', 'learn sql']},
            {"name": 'PostgreSQL / MySQL', "keywords": ['postgresql', 'mysql', 'postgres']},
            {"name": 'MongoDB / NoSQL', "keywords": ['mongodb', 'nosql', 'document database']},
            {"name": 'Redis & Caching', "keywords": ['redis', 'caching', 'in-memory']},
        ]
    },
    {
        "id": 'algorithms', "name": 'Algorithms', "description": 'Sorting, searching, DP, graph algorithms — interview ready.',
        "lucideIcon": 'GitBranch', "color": '#ec4899', "tier": 4, "prerequisites": ['data-structures'], "connections": ['system-design'],
        "subtopics": [
            {"name": 'Sorting & Searching', "keywords": ['sorting', 'searching', 'binary search', 'merge sort']},
            {"name": 'Dynamic Programming', "keywords": ['dynamic programming', 'dp ', 'memoization', 'tabulation']},
            {"name": 'Greedy Algorithms', "keywords": ['greedy', 'greedy algorithm']},
            {"name": 'Backtracking', "keywords": ['backtracking', 'recursion', 'n-queens']},
            {"name": 'Graph Algorithms', "keywords": ['dijkstra', 'shortest path', 'topological sort', 'graph algorithm']},
            {"name": 'Bit Manipulation', "keywords": ['bit manipulation', 'bitwise', 'xor']},
        ]
    },
    {
        "id": 'machine-learning', "name": 'ML / AI', "description": 'Neural networks, deep learning, and AI fundamentals.',
        "lucideIcon": 'Brain', "color": '#a855f7', "tier": 4, "prerequisites": ['python'], "connections": ['system-design'],
        "subtopics": [
            {"name": 'ML Basics', "keywords": ['machine learning basics', 'machine learning fundamentals', 'supervised learning', 'intro to ml']},
            {"name": 'Deep Learning', "keywords": ['deep learning', 'neural network', 'cnn', 'rnn']},
            {"name": 'NLP', "keywords": ['natural language', 'nlp', 'text processing']},
            {"name": 'TensorFlow / PyTorch', "keywords": ['tensorflow', 'pytorch', 'keras']},
        ]
    },
    {
        "id": 'git-vcs', "name": 'Git & VCS', "description": 'Version control, branching, collaboration workflows.',
        "lucideIcon": 'GitMerge', "color": '#f05032', "tier": 4, "prerequisites": [], "connections": ['devops'],
        "subtopics": [
            {"name": 'Git Basics', "keywords": ['git basics', 'git fundamentals', 'learn git', 'version control basics']},
            {"name": 'Branching & Merging', "keywords": ['git branch', 'git merge', 'git rebase']},
            {"name": 'GitHub / GitLab', "keywords": ['github', 'gitlab', 'pull request', 'code review']},
        ]
    },
    {
        "id": 'devops', "name": 'DevOps & Cloud', "description": 'Docker, CI/CD, AWS/GCP — deploy and scale applications.',
        "lucideIcon": 'Cloud', "color": '#06b6d4', "tier": 5, "prerequisites": [], "connections": ['system-design'],
        "subtopics": [
            {"name": 'Docker', "keywords": ['docker', 'containerization', 'dockerfile']},
            {"name": 'CI/CD', "keywords": ['ci/cd', 'continuous integration', 'github actions', 'jenkins']},
            {"name": 'AWS / Cloud', "keywords": ['aws', 'amazon web services', 'cloud computing', 'gcp', 'azure']},
            {"name": 'Kubernetes', "keywords": ['kubernetes', 'k8s', 'container orchestration']},
        ]
    },
    {
        "id": 'system-design', "name": 'System Design', "description": 'Architect scalable, distributed systems like the pros.',
        "lucideIcon": 'Blocks', "color": '#f59e0b', "tier": 5, "prerequisites": ['databases', 'algorithms'], "connections": [],
        "subtopics": [
            {"name": 'Design Principles', "keywords": ['system design basics', 'design principles', 'scalability']},
            {"name": 'Load Balancing', "keywords": ['load balancing', 'horizontal scaling']},
            {"name": 'Microservices', "keywords": ['microservices', 'distributed systems', 'service oriented']},
            {"name": 'Caching & CDN', "keywords": ['caching strategy', 'cdn', 'content delivery']},
        ]
    }
]


def seed_gamification_db(db: Session):
    """Seed the database with default achievements and skill nodes if they don't exist."""
    try:
        # 1. Seed Achievements
        for ach in DEFAULT_ACHIEVEMENTS:
            if not db.query(Achievement).filter(Achievement.id == ach["id"]).first():
                db.add(Achievement(**ach))
                
        # 2. Seed Skill Nodes
        for node in SKILL_TREE_SEED:
            existing = db.query(SkillNode).filter(SkillNode.id == node["id"]).first()
            if not existing:
                sn = SkillNode(
                    id=node["id"],
                    name=node["name"],
                    description=node["description"],
                    lucide_icon=node["lucideIcon"],
                    color=node["color"],
                    tier=node["tier"],
                    prerequisites=node["prerequisites"],
                    connections=node["connections"]
                )
                db.add(sn)
                for sub in node["subtopics"]:
                    db.add(SkillSubtopic(
                        node_id=node["id"],
                        name=sub["name"],
                        keywords=sub["keywords"]
                    ))
        db.commit()
    except Exception as e:
        print(f"[Gamification Seed] Error: {e}")
        db.rollback()


def get_skill_tree_with_progress(db: Session, user_id: str) -> List[Dict[str, Any]]:
    """Returns the skill tree exactly as expected by SkillTree.jsx."""
    nodes = db.query(SkillNode).all()
    result = []
    for n in nodes:
        node_dict = {
            "id": n.id,
            "name": n.name,
            "description": n.description,
            "lucideIcon": n.lucide_icon,
            "color": n.color,
            "tier": n.tier,
            "prerequisites": n.prerequisites,
            "connections": n.connections,
            "subtopics": []
        }
        for sub in n.subtopics:
            node_dict["subtopics"].append({
                "name": sub.name,
                "keywords": sub.keywords
            })
        result.append(node_dict)
    return result

def get_user_badges(db: Session, user_id: str) -> List[Dict[str, Any]]:
    user_achs = db.query(UserAchievement).filter(UserAchievement.user_id == user_id).all()
    unlocked_ids = {ua.achievement_id for ua in user_achs}
    
    all_achs = db.query(Achievement).all()
    result = []
    for a in all_achs:
        result.append({
            "id": a.id,
            "name": a.name,
            "description": a.description,
            "icon": a.icon,
            "color": a.color,
            "xp_reward": a.xp_reward,
            "unlocked": a.id in unlocked_ids
        })
    return result

def generate_daily_quests(db: Session, user_id: str) -> List[Dict[str, Any]]:
    """Generate 3 daily quests if they don't exist for today."""
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    quests = db.query(DailyQuest).filter(DailyQuest.user_id == user_id, DailyQuest.date == today).all()
    
    if not quests:
        q1 = DailyQuest(user_id=user_id, date=today, quest_type="productive_time", title="Log 2h of Productive Time", target_value=7200, xp_reward=30)
        q2 = DailyQuest(user_id=user_id, date=today, quest_type="limit_distraction", title="Keep Distractions Under 30m", target_value=1800, current_value=0, xp_reward=20)
        q3 = DailyQuest(user_id=user_id, date=today, quest_type="watch_chapters", title="Complete 2 Learning Chapters", target_value=2, xp_reward=40)
        db.add_all([q1, q2, q3])
        db.commit()
        quests = [q1, q2, q3]
        
    return [{
        "id": q.id,
        "title": q.title,
        "target_value": q.target_value,
        "current_value": q.current_value,
        "is_completed": q.is_completed,
        "xp_reward": q.xp_reward
    } for q in quests]
