from flask import Flask, session, redirect, url_for, request, render_template, jsonify
import random
import base64
import sqlite3
import sqlalchemy
from flask import g
import hashlib
import time
import os

# App setup
app = Flask(__name__)
app.secret_key = b'\xbfEdVSb\xc6\x91Q\x02\x1c\xa7cN\xba$'
app.dev = False

# Default directories and values
DATABASE = './database.db'
IMAGE_DIR = 'static/images/'
NUM_IMAGES = 3
experiment_complete = False

# Page URLs
WAIT_PAGE = 'wait'
DASHBOARD_PAGE = 'dashboard'
SUBMIT_MODS_PAGE = 'submit_mods'
SUBMIT_OBS_PAGE = 'submit_obs'
CHECK_MOD_SUBMITTED_PAGE = 'check_mod_submitted'
CHECK_OBS_SUBMITTED_PAGE = 'check_obs_submitted'
WORK_PAGE = 'work'
DONE_PAGE = 'done'
NARRATIVE_PAGE = 'narrative'
CONSENT_PAGE = 'consent'
EXPERIMENT_COMPLETE_PAGE = 'experiment_complete'
POLL_WORK_READY_PAGE = 'poll_work_ready'
MARK_WORK_READY_PAGE = 'mark_work_ready'

# Get parameters in URL
TURK_ID_VAR = 'workerId'
ASSIGNMENT_ID_VAR = 'assignmentId'
CONSENT_VAR = 'consent'
JOB_VAR = 'j'
CONDITION_VAR = 'c'
WAS_OBSERVER_VAR = 'was_observer'
IS_LAST_VAR = 'isLast'
WAS_WAITING_VAR = 'was_waiting'

# Possible values for Get parameters
JOB_MOD_VAL = 'mod'
JOB_OBS_VAL = 'obs'
CONDITION_CON_VAL = 'con'
CONDITION_EXP_VAL = 'exp'

# Close database
@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    # if db is not None:
        # db.close()

# Get database reference
def get_db():
    db = getattr(g, '_database', None)
    if db is None: # Launch database if it hasn't been
        db = g._database = sqlalchemy.create_engine(os.environ['DATABASE_URL'], pool_size = 15)
    return db

# Query database
def query_db(query, args=(), one=False):
    cur = get_db().execute(sqlalchemy.text(query), list(args))
    rv = cur.fetchall()
    # get_db().commit()
    cur.close()
    return (rv[0] if rv else None) if one else rv

# Load image paths from images folder to database
def load_images_to_db():
    files = os.listdir(IMAGE_DIR)
    for f in files:
        db.execute(sqlalchemy.text('insert into images(img_path) VALUES(:path)'), path=IMAGE_DIR + f)
    # db.commit()

# App initialization
with app.app_context():
    db = get_db()

    # Load database schema
    with open('db.schema', mode='r') as f:
        db.execute(sqlalchemy.text(f.read()))
        # db.cursor().executescript(f.read())
    # db.commit()

    # Load images (if none are loaded)
    out = db.execute('select count(*) from images')
    count = out.fetchall()[0][0]
    if count == 0:
        load_images_to_db()

# Gets subset of all images to be displayed
def get_array_subset(array, num_vals, cannot_contain):
    assert len(array) - len(cannot_contain) >= num_vals
    subset = []
    while len(subset) < num_vals: # Add num_vals images
        i = random.randint(0, len(array) - 1)
        val = array[i]

        # Don't add image if it was already seen previously in observer role
        if val not in subset and val not in cannot_contain:
            subset.append(val)
    return subset

# Dashboard page
@app.route('/' + DASHBOARD_PAGE)
def dashboard():
    # Get workers in control group and pairs in experimental group
    participants=db.execute(sqlalchemy.text('select * from participants')).fetchall()
    control = [p for p in participants if p[2] == CONDITION_CON_VAL]
    pairs = db.execute(sqlalchemy.text('select * from pairs')).fetchall()

    done_class = 'class="worker-done"' # Class that marks worker/pair as finished on dashboard

    # Construct control table elements
    control_html = ''
    for c in control:
        worker_id = c[1]
        worker_done = db.execute(sqlalchemy.text('select response from consent where turk_id=:worker_id'), worker_id=worker_id).fetchone() is not None
        done_text = done_class if worker_done else ''
        control_html += '<tr><th {} scope="row">{}</th><td {}>{}</td></tr>'.format(done_text, c[0], done_text, worker_id)

    # Construct experimental table elements
    experiment_html = ''
    for p in pairs:
        pair_id = p[0]
        print('dashboard pair_id: {}'.format(pair_id))
        mod_id = db.execute(sqlalchemy.text('select parts.turk_id from pairs p, participants parts where p.id=:pair_id and p.mod_id=parts.user_id'), pair_id=pair_id).fetchone()
        print('dashboard mod_id: {}'.format(mod_id))
        if mod_id is not None:
            obs_id = db.execute(sqlalchemy.text('select parts.turk_id from pairs p, participants parts where p.id=:pair_id and p.obs_id=parts.user_id'), pair_id=pair_id).fetchone()
            worker_done = db.execute(sqlalchemy.text('select response from consent where turk_id=:mod_id'), mod_id=mod_id[0]).fetchone() is not None
            obs_skipped = db.execute(sqlalchemy.text('select response from consent where turk_id=:obs_id'), obs_id=obs_id[0]).fetchone() is not None
            done_text = done_class if worker_done or obs_skipped else ''
            work_ready = db.execute(sqlalchemy.text('select work_ready from pairs where id=:pair_id'), pair_id=pair_id).fetchone()[0]
            print('dashboard work_ready: {}'.format(work_ready))
            work_ready_btn = '<button ' + ('disabled' if work_ready is not None else '') + ' onclick="markPairWorking(\'' + str(pair_id) + '\', this)">Start Work</button>'
            unpaired_mod = mod_id[0] is not None and obs_id[0] is None
            unpaired_obs = mod_id[0] is None and obs_id[0] is not None
            if (unpaired_mod or unpaired_obs) and not experiment_complete:
                work_ready_btn = ''

            mod_id_text = '' if p[2] is None else p[2]
            obs_id_text = '' if p[1] is None else p[1]
            experiment_html += '<tr><th {} scope="row">{}{}</th><td {}>{}</td><td {}>{}</td></tr>'.format(done_text, pair_id, work_ready_btn, done_text, mod_id_text, done_text, obs_id_text)

    return render_template('dashboard.html', control_html=control_html, experiment_html=experiment_html, experiment_complete=experiment_complete)

# Marks pair as ready to be moved to work page
@app.route("/" + MARK_WORK_READY_PAGE, methods=['POST'])
def mark_work_ready():
    json = request.json
    pair_id = json['pair_id']
    db.execute(sqlalchemy.text('update pairs set work_ready=:true where id=:pair_id'), true=True, pair_id=pair_id)
    return jsonify(status='success')

# Mark experiment as completed on dashboard page
@app.route("/" + EXPERIMENT_COMPLETE_PAGE, methods=['POST'])
def experiment_finished():
    global experiment_complete
    experiment_complete = True

    unpaired_mods = db.execute(sqlalchemy.text('select mod_id from pairs where obs_id is null')).fetchall()
    for mod_id in unpaired_mods:
        db.execute(sqlalchemy.text('update participants set edge_case=:last where user_id=:mod_id'), last='Last', mod_id=mod_id)

    return jsonify(status='success')

# Narrative page
@app.route("/" + NARRATIVE_PAGE)
def narrative():
    session.clear()

    turkId = request.args.get(TURK_ID_VAR)
    assignmentId = request.args.get(ASSIGNMENT_ID_VAR)

    session[TURK_ID_VAR] = turkId
    session[ASSIGNMENT_ID_VAR] = assignmentId
    session[WAS_WAITING_VAR] = None

    return render_template('narrative.html', turkId=turkId)

# Consent page
@app.route("/" + CONSENT_PAGE)
def consent():
    turkId = session[TURK_ID_VAR]
    db.execute(sqlalchemy.text('insert into consent(turk_id, response) VALUES(:turk_id, :no)'), turk_id=turkId, no='No')
    return render_template('consent.html')

# Done page
@app.route("/" + DONE_PAGE)
def done():
    turk_id = session.get(TURK_ID_VAR)
    consent = request.args.get(CONSENT_VAR)

    if consent == 'Yes':
        db.execute(sqlalchemy.text('update consent set response=:consent where turk_id=:turk_id'), consent=consent, turk_id=turk_id)

    return render_template('done.html', turk_id=turk_id, task_finished=True)

# returns True if the person got paired, or False if a new pair was created
def check_edge_case(user_id):
    obs_ids = query_db('select obs_id from pairs where mod_id IS NULL')
    paired = False
    if obs_ids is not None:
        print('edge obs_id: {}'.format(user_id))
        for obs_id in obs_ids: # Trying to pair with an existing observer
            edge_case = db.execute(sqlalchemy.text('select edge_case from participants where user_id=:obs_id'), obs_id=obs_id[0]).fetchone() # Checking if observer finished task unpaired
            print('edge case: {}'.format(edge_case))
            if edge_case is not None and edge_case[0] != 'Unpaired observer':
                print('seems paired {}'.format(user_id))
                db.execute(sqlalchemy.text('update pairs set mod_id=:uid where obs_id=:obs_id'), uid=user_id, obs_id=obs_id[0]) # Pairing worker
                paired = True
                break
    if not paired:
        print('edge not paired {}'.format(user_id))
        db.execute(sqlalchemy.text('insert into pairs(mod_id) VALUES(:uid)'), uid=user_id) # Creating new pair
    
    return paired

# Wait page
@app.route("/" + WAIT_PAGE)
def wait():
    uid = session[TURK_ID_VAR]
    pid = 0

    # Checking if worker was already on the wait page (i.e. a refresh occurred)
    if session[WAS_WAITING_VAR] is not None:
        job = session[JOB_VAR]
        user_id = db.execute(sqlalchemy.text('select user_id from participants where turk_id=:uid'), uid=uid).fetchone()[0]
        print('JOB IS ' + str(job) + ' AND USER ID IS ' + str(user_id)) # TODO
        if job == JOB_MOD_VAL:
            pair_id = db.execute(sqlalchemy.text('select id from pairs where mod_id=:uid'), uid=uid).fetchone()[0]
        else:
            pair_id = db.execute(sqlalchemy.text('select id from pairs where obs_id=:uid'), uid=uid).fetchone()[0]
        return render_template('wait.html', pair_id=pair_id)
    else:
        session[WAS_WAITING_VAR] = 'Yes'

    was_observer = session.get(WAS_OBSERVER_VAR)
    session[WAS_OBSERVER_VAR] = None

    # Exiting early if worker has already been added to system
    pid = db.execute(sqlalchemy.text('select user_id from participants where turk_id=:uid'), uid=uid).fetchone()
    worker_exists = pid is not None

    if worker_exists and not was_observer:
        return render_template('wait.html')

    if experiment_complete:
        return render_template('done.html', turk_id=uid, task_finished=False)

    # Determining worker condition
    cond = request.args.get(CONDITION_VAR)
    if was_observer is not None: # Condition was assigned as URL param (testing)
        cond = CONDITION_EXP_VAL
    elif cond is None: # Condition is assigned randomly (experiment)
        cond = CONDITION_CON_VAL if random.random() < 0.5 else CONDITION_EXP_VAL
    session[CONDITION_VAR] = cond

    if worker_exists is False:
        result = db.execute(sqlalchemy.text('insert into participants(turk_id, condition) VALUES(:uid, :cond) '), uid=uid, cond=cond)
        pid = db.execute(sqlalchemy.text('select user_id from participants where turk_id=:uid'), uid=uid).fetchone()[0]
    session['pid'] = pid

    # Determining worker job
    print('work cond: {}'.format(cond))
    print('work was_observer: {}'.format(was_observer))
    print('work worker_exists: {}'.format(worker_exists))
    if was_observer is not None:
        job = JOB_MOD_VAL
        print('cond 2')
    else: 
        if cond == CONDITION_CON_VAL: # Worker is in control condition they've been an observer
            job = JOB_MOD_VAL
            print('cond 1')
        else: 
            unpaired_pairs = db.execute(sqlalchemy.text('select id from pairs where mod_id IS NULL and obs_id!=:uid'), uid=pid).fetchall()
            print('wait unpaired_pairs: {}'.format(unpaired_pairs))
            print('cond 3')
            if unpaired_pairs is not None and len(unpaired_pairs) == 1 and unpaired_pairs[0] is not None and unpaired_pairs[0][0] == 1:
                job = JOB_MOD_VAL
                print('cond 4')

        # unpaired_obs = db.execute(sqlalchemy.text('select obs_id from pairs where mod_id IS NULL and obs_id!=:uid'), uid=uid).fetchone()
        # if unpaired_obs is not None:
        #     for obs_id in unpaired_obs: # Checking if all unpaired observers are already finished with task and can't be paired
        #         edge_case = db.execute(sqlalchemy.text('select edge_case from participants where turk_id=:obs_id'), obs_id=obs_id[0]).fetchone()
        #         if edge_case is not None and edge_case[0] != 'Unpaired observer':
        #             job = JOB_MOD_VAL
        #             break
            else:
                job = JOB_OBS_VAL
                print('cond 5')
    session[JOB_VAR] = job


    # Worker pairing logic
    if cond == CONDITION_EXP_VAL: # Experimental condition
        # check = db.execute(sqlalchemy.text('select turk_id from participants where turk_id=:uid'), uid=uid).fetchone() # Check if worker is already in the system
        if worker_exists is False: # Worker was not previously in system
            if job == JOB_MOD_VAL: # Moderator role
                paired = check_edge_case(pid)
            elif job == JOB_OBS_VAL: # Observer role
                # mod_id = query_db('select mod_id from pairs where obs_id IS NULL', one=True)
                mod_id = db.execute(sqlalchemy.text('select mod_id from pairs where obs_id IS NULL')).fetchone()
                print('job_mod: {}'.format(mod_id))
                if mod_id is None: # Creating new pair
                    db.execute(sqlalchemy.text('insert into pairs(obs_id) VALUES(:uid)'), uid=pid)
                else: # Pairing with existing moderator
                    db.execute(sqlalchemy.text('update pairs set obs_id=:uid where mod_id=:mod_id'), uid=pid, mod_id=mod_id[0])
        elif was_observer is not None: # Worker was previously an observer and is now a moderator
            paired = check_edge_case(pid)

    if cond == CONDITION_EXP_VAL:
        if job == JOB_MOD_VAL:
            pair_id = db.execute(sqlalchemy.text('select id from pairs where mod_id=:uid'), uid=pid).fetchone()[0]
        else:
            pair_id = db.execute(sqlalchemy.text('select id from pairs where obs_id=:uid'), uid=pid).fetchone()[0]

        return render_template('wait.html', pair_id=pair_id)
    else:
        return redirect(url_for(WORK_PAGE))

# Waiting worker polls server to see if they've been flagged to start working
@app.route("/" + POLL_WORK_READY_PAGE, methods=['POST'])
def poll_work_ready():
    json = request.json

    pair_id = json['pair_id']
    work_ready = db.execute(sqlalchemy.text('select work_ready from pairs where id=:pair_id'), pair_id=pair_id).fetchone()[0]

    if work_ready is not None:
        return jsonify(status='success')
    else:
        return jsonify(status='failure')

# Submits moderator decisions to database
@app.route("/" + SUBMIT_MODS_PAGE, methods=['POST'])
def accept_moderations():
    json = request.json

    pair_id = json['pair_id']
    img_ids = json['img_ids']
    decisions = json['decisions']

    for i in range(NUM_IMAGES):
        if pair_id == 0: # Worker was in control group
            query = 'insert into moderations(decision, img_id) VALUES({}, {})'.format(decisions[i], img_ids[i])
            db.execute(sqlalchemy.text(query)).fetchone()
        else: # Worker was in experimental group
            query = 'insert into moderations(decision, img_id, pair_id) VALUES({}, {}, {})'.format(decisions[i], img_ids[i], pair_id)
            db.execute(sqlalchemy.text(query)).fetchone()

    db.execute(sqlalchemy.text('update pairs set mod_submitted=:sub where id=:pair_id'), sub=True, pair_id=pair_id)
    return jsonify(status='success')

# Observer polls if moderator has submitted their responses
@app.route("/" + CHECK_MOD_SUBMITTED_PAGE, methods=['POST'])
def check_mod_submitted():
    json = request.json
    pair_id = json['pair_id']

    query = 'select mod_submitted from pairs where id={}'.format(pair_id)
    val = db.execute(sqlalchemy.text(query)).fetchall()

    if val is not None and val[0] is not None and val[0][0] is not None and val[0][0]:
        return jsonify(status='success', submitted='true')
    else:
        return jsonify(status='success', submitted='false')

# Moderator polls if observer has submitted their responses
@app.route("/" + CHECK_OBS_SUBMITTED_PAGE, methods=['POST'])
def check_obs_submitted():
    json = request.json
    pair_id = json['pair_id']

    query = 'select obs_submitted from pairs where id={}'.format(pair_id)
    val = db.execute(sqlalchemy.text(query)).fetchall()

    if val is not None and val[0] is not None and val[0][0] is not None and val[0][0]:
        return jsonify(status='success', submitted='true')
    else:
        return jsonify(status='success', submitted='false')

# Submits observer responses to database
@app.route("/" + SUBMIT_OBS_PAGE, methods=['POST'])
def accept_observations():
    json = request.json

    db.execute(sqlalchemy.text('insert into observations(pair_id, obs_text) VALUES(:pair, :text)'), pair=json['pair_id'], text=json['obs_text'])

    db.execute(sqlalchemy.text('update pairs set obs_submitted=:sub where id=:id'), sub=True, id=json['pair_id'])

    session[WAS_OBSERVER_VAR] = 'true'
    return jsonify(status='success')

def get_user_color():
    politicizing = True
    if politicizing:
        return '#ff0000' if random.getrandbits(1) else '#0000ff'
    else:
        return '#{:06x}'.format(random.randint(0, 256**3))

# Work page where observing/moderation occurs
@app.route("/" + WORK_PAGE)
def work():
    if 'user_color' in session.keys():
        user_color = session['user_color']
    else: 
        user_color = session['user_color'] = get_user_color()
    turkId = session.get(TURK_ID_VAR)
    job = session[JOB_VAR]
    condition = session[CONDITION_VAR]
    session[WAS_WAITING_VAR] = None
    pid = session.get('pid')

    # Getting current pair and corresponding observer and moderator IDs
    if condition == CONDITION_EXP_VAL:
        if job == JOB_MOD_VAL:
            obs, mod = db.execute(sqlalchemy.text('select obs_id, mod_id from pairs where mod_id=:turk_id'), turk_id=pid).fetchone()
            page = 'moderation'
        else:
            print('work pid: {}'.format(pid))
            holder = db.execute(sqlalchemy.text('select obs_id, mod_id from pairs where obs_id=:turk_id'), turk_id=pid).fetchone()
            print('work holder: {}'.format(holder))
            if holder is not None:
                obs, mod = holder

            if mod is None: # Observer cannot work without paired moderator (edge case)
                db.execute(sqlalchemy.text('insert into consent(turk_id, response) VALUES(:turk_id, :no)'), turk_id=turkId, no='No')
                db.execute(sqlalchemy.text('update participants set edge_case=:obs where turk_id=:turk_id'), obs='Unpaired observer', turk_id=turkId)
                return render_template('done.html', turk_id=turkId, task_finished=False)

            page = 'observation'
        if obs is None:
            pair_id = db.execute(sqlalchemy.text('select id from pairs where obs_id IS NULL and mod_id=:mod'), mod=mod).fetchone()[0]
        else:
            pair_id = db.execute(sqlalchemy.text('select id from pairs where obs_id=:obs and mod_id=:mod'), obs=obs, mod=mod).fetchone()[0]
    else:
        pair_id = 0
        page = 'moderation'

    # Constructing room name as concatenation of moderator and observer IDs (only in experimental condition)
    room_name = 'pair-{}'.format(pair_id) if condition == CONDITION_EXP_VAL else ''

    # Getting first pair that isn't an unpaired observer
    all_pairs = db.execute(sqlalchemy.text('select * from pairs order by id ASC'))
    first_pair_with_mod = 0
    for p in all_pairs:
        mod_id = p[2]
        if mod_id is not None:
            first_pair_with_mod = p[0]
            break

    # Checking for edge cases
    edge_check = db.execute(sqlalchemy.text('select edge_case from participants where turk_id=:turk_id'), turk_id=turkId).fetchone()
    if pair_id == first_pair_with_mod and job == JOB_MOD_VAL:
        edge_case = 'First'
        db.execute(sqlalchemy.text('update participants set edge_case=:edge where turk_id=:turk_id'), edge=edge_case, turk_id=turkId)
    elif edge_check is not None and edge_check[0] == 'Last':
        edge_case = 'Last'
        condition = CONDITION_CON_VAL # Simulating control condition if this is the last worker (no observer)
    else:
        edge_case = None

	# Getting all image URLs in database
    # all_imgs = query_db('select img_path from images')
    all_imgs = db.execute(sqlalchemy.text('select img_path from images')).fetchall()

    chosen_imgs = db.execute(sqlalchemy.text('select img_id from chosen_imgs where pair_id=:pair_id'), pair_id=pair_id).fetchall() # Check if worker's pair has already been assigned images
    print('chosen_images: {}'.format(chosen_imgs))
    if chosen_imgs is None or len(chosen_imgs) == 0: # Images have not already been assigned to paired partner
        curr_mod = db.execute(sqlalchemy.text('select mod_id from pairs where id=:pair_id'), pair_id=pair_id).fetchone()
        cannot_contain = []
        if curr_mod is not None:
            # Checking if worker was previously paired (as an observer)
            last_pair = db.execute(sqlalchemy.text('select id from pairs where obs_id=:curr_mod'), curr_mod=curr_mod[0]).fetchone()
            if last_pair is not None:
                # Finding images that were previously seen by this worker so they don't moderate the same ones
                cannot_contain_ids = db.execute(sqlalchemy.text('select img_id from moderations where pair_id=:last'), last=last_pair[0])
                for id in cannot_contain_ids:
                    path = db.execute(sqlalchemy.text('select img_path from images where img_id=:id'), id=id[0]).fetchone()
                    cannot_contain.append(path)
        subset = get_array_subset(all_imgs, NUM_IMAGES, cannot_contain) # Randomly selecting images for the task
        if pair_id != 0:
            # Setting images as chosen so paired partner sees the same ones
            for s in subset:
                id = db.execute(sqlalchemy.text('select img_id from images where img_path=:path'), path=s[0]).fetchone()[0]
                db.execute(sqlalchemy.text('insert into chosen_imgs(img_id, pair_id) VALUES(:id, :pair)'), id=id, pair=pair_id)
    else:
        subset = []
        for img_id in chosen_imgs: # Getting images that have already been assigned to partner
            path = db.execute(sqlalchemy.text('select img_path from images where img_id=:img_id'), img_id=img_id[0]).fetchone()
            subset.append(path)

    # Extracting image URLs from chosen subset and their corresponding IDs
    print('work subset: {}'.format(subset))
    img_subset = [str(s[0]) for s in subset]
    img_ids = [db.execute(sqlalchemy.text('select img_id from images where img_path=:img_subset'), img_subset=img_subset[i]).fetchone()[0] for i in range(len(img_subset))]

    return render_template('work.html', page=page, condition=condition, room_name=room_name, imgs=img_subset, img_ids=img_ids, img_count=NUM_IMAGES, pair_id=pair_id, edge_case=edge_case, user_color = user_color)
