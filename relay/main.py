from relay import app
from flask import (
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    send_from_directory,
)
from relay.db import (
    fetch_random_item,
    get_connection,
    get_user_by_email,
    get_user_by_user_id,
    insert_user,
)
import uuid
from datetime import datetime
import unicodedata
import os
from urllib.parse import urlparse

import cloudinary.uploader
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from functools import wraps

ALLOWED_ICON_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif'}
MAX_NICKNAME_LENGTH = 32

MAX_TITLE_LENGTH = 60
MAX_POST_LENGTH = 280


@app.context_processor
def inject_notifications():
    if 'user_id' not in session:
        return dict(revival_notifications=[])
    
    user_id = session['user_id']
    with get_connection() as con:
        revival_rows = con.execute("""
            SELECT 
                rn.notify_id,
                rn.created_at,
                rn.picker_id,
                picker.nickname,
                picker.icon_path,
                i.title,
                i.category
            FROM revival_notify rn
            JOIN ideas i ON rn.idea_id = i.idea_id
            LEFT JOIN mypage picker ON rn.picker_id = picker.user_id
            WHERE rn.author_id = ?
            ORDER BY rn.created_at DESC
        """, (user_id,)).fetchall()

    revival_notifications = []
    for row in revival_rows:
        revival_notifications.append({
            'notify_id': row[0],
            'created_at': row[1],
            'picker_id': row[2],
            'picker_nickname': row[3] if row[3] else '不明なユーザー',
            'picker_icon_path': row[4],
            'idea_title': row[5],
            'category': row[6]
        })
    
    return dict(revival_notifications=revival_notifications)


def calculate_text_length(text):
    length = 0
    for ch in text:
        if unicodedata.east_asian_width(ch) in ('F', 'W'):
            length += 2
        else:
            length += 1
    return length


def login_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            next_url = request.url
            return redirect(url_for('login', next=next_url))
        return view_func(*args, **kwargs)

    return wrapper



def store_icon_file(icon_file, extension):
    if app.config.get('USE_CLOUDINARY'):
        icon_file.stream.seek(0)
        upload_options = {'resource_type': 'image'}
        folder = os.environ.get('CLOUDINARY_UPLOAD_FOLDER')
        if folder:
            upload_options['folder'] = folder
        upload_result = cloudinary.uploader.upload(icon_file, **upload_options)
        return upload_result.get('secure_url')

    uploads_dir = app.config['UPLOAD_FOLDER']
    os.makedirs(uploads_dir, exist_ok=True)
    stored_filename = f"{uuid.uuid4().hex}{extension}"
    save_path = os.path.join(uploads_dir, stored_filename)
    icon_file.stream.seek(0)
    icon_file.save(save_path)
    return os.path.join('uploads', stored_filename)


def delete_icon_file(icon_path):
    if not icon_path:
        return
    if icon_path.startswith('http'):
        if app.config.get('USE_CLOUDINARY'):
            public_id = _extract_public_id(icon_path)
            if public_id:
                cloudinary.uploader.destroy(public_id, invalidate=True)
        return
    if icon_path.startswith('uploads/'):
        filename = icon_path.split('/', 1)[1]
        absolute_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if os.path.exists(absolute_path):
        os.remove(absolute_path)


def _extract_public_id(url: str) -> str | None:
    parsed = urlparse(url)
    path_parts = parsed.path.strip('/').split('/')
    try:
        upload_index = path_parts.index('upload')
    except ValueError:
        return None
    public_parts = path_parts[upload_index + 1 :]
    if public_parts and public_parts[0].startswith('v') and public_parts[0][1:].isdigit():
        public_parts = public_parts[1:]
    if not public_parts:
        return None
    public_id_with_ext = '/'.join(public_parts)
    public_id, _ = os.path.splitext(public_id_with_ext)
    return public_id or None


def get_current_user_id():
    return session.get('user_id')


@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/')
@login_required
def index():
    with get_connection() as con:
        db_items = con.execute(
            """
            SELECT 
                i.idea_id,
                i.title,
                i.detail,
                i.category,
                i.user_id,
                i.created_at,
                u.nickname
            FROM ideas i
            LEFT JOIN mypage u ON i.user_id = u.user_id
            ORDER BY i.created_at DESC
            """
        ).fetchall()

    items = []

    for row in db_items:
        items.append({
            'idea_id': row[0],
            'title': row[1],
            'detail': row[2],
            'category': row[3],
            'user_id': row[4],
            'created_at': row[5],
            'nickname': row[6]
        })
    
    user_name = session['nickname']

    return render_template(
        'index.html',
        items=items,
        user_name=user_name
    )

@app.route('/form')
@login_required
def form():
    return render_template(
        'form.html'
    )


@app.route('/inheritance/<idea_id>')
@login_required
def inheritance_form(idea_id):
    user_id = session['user_id']
    
    with get_connection() as con:
        idea_row = con.execute(
            "SELECT idea_id, title, detail, category, user_id, created_at FROM ideas WHERE idea_id = ?",
            (idea_id,)
        ).fetchone()
        
        if not idea_row:
            flash('アイデアが見つかりません。')
            return redirect(url_for('mypage'))
        
        parent_user_row = con.execute(
            "SELECT user_id, nickname FROM mypage WHERE user_id = ?",
            (idea_row[4],)
        ).fetchone()
    
    idea = {
        'idea_id': idea_row[0],
        'title': idea_row[1],
        'detail': idea_row[2],
        'category': idea_row[3],
        'user_id': idea_row[4],
        'created_at': idea_row[5],
        'author_nickname': parent_user_row[1] if parent_user_row else '不明なユーザー'
    }
    
    return render_template(
        'inheritance_form.html',
        idea=idea
    )


@app.route('/inheritance/<idea_id>/save', methods=['POST'])
@login_required
def save_inheritance(idea_id):
    user_id = session['user_id']
    add_point = request.form.get('add_point', '').strip()
    add_detail = request.form.get('add_detail', '').strip()
    parent_idea_id = request.form.get('parent_idea_id')
    parent_user_id = request.form.get('parent_user_id')

    if not add_point:
        flash('追加したポイントを入力してください。')
        return redirect(url_for('inheritance_form', idea_id=idea_id))

    if calculate_text_length(add_point) > 64:
        flash('追加したポイントは全角32文字（半角64文字）以内で入力してください。')
        return redirect(url_for('inheritance_form', idea_id=idea_id))

    with get_connection() as con:
        # 既存の継承レコードがあるか確認
        existing = con.execute(
            "SELECT inheritance_id FROM idea_inheritance WHERE parent_idea_id = ? AND child_user_id = ? AND child_idea_id IS NULL",
            (parent_idea_id, user_id)
        ).fetchone()

        inheritance_id = str(uuid.uuid4())
        created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        if existing:
            # 既存のレコードを更新
            con.execute(
                """
                UPDATE idea_inheritance 
                SET add_point = ?, add_detail = ?, created_at = ?
                WHERE inheritance_id = ?
                """,
                (add_point, add_detail if add_detail else None, created_at, existing[0])
            )
        else:
            # 新規レコードを作成
            con.execute(
                """
                INSERT INTO idea_inheritance 
                (inheritance_id, parent_idea_id, parent_user_id, child_idea_id, child_user_id, add_point, add_detail, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (inheritance_id, parent_idea_id, parent_user_id, None, user_id, add_point, add_detail if add_detail else None, created_at)
            )

    flash('継承情報を保存しました。')
    return redirect(url_for('mypage'))


@app.route('/inheritance/<idea_id>/post', methods=['POST'])
@login_required
def post_inheritance(idea_id):
    user_id = session['user_id']
    add_point = request.form.get('add_point', '').strip()
    add_detail = request.form.get('add_detail', '').strip()
    parent_idea_id = request.form.get('parent_idea_id')
    parent_user_id = request.form.get('parent_user_id')

    if not add_point:
        flash('追加したポイントを入力してください。')
        return redirect(url_for('inheritance_form', idea_id=idea_id))

    if calculate_text_length(add_point) > 64:
        flash('追加したポイントは全角32文字（半角64文字）以内で入力してください。')
        return redirect(url_for('inheritance_form', idea_id=idea_id))

    with get_connection() as con:
        # 親アイデアの情報を取得
        parent_idea = con.execute(
            "SELECT title, detail, category FROM ideas WHERE idea_id = ?",
            (parent_idea_id,)
        ).fetchone()

        if not parent_idea:
            flash('継承元のアイデアが見つかりません。')
            return redirect(url_for('mypage'))

        # 新しいアイデアを作成（継承元の情報をベースに）
        child_idea_id = str(uuid.uuid4())
        created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # タイトルと詳細を継承元から取得（必要に応じて編集可能にする場合は変更）
        child_title = parent_idea[0]  # 親のタイトルを使用
        child_detail = parent_idea[1]  # 親の詳細を使用
        child_category = parent_idea[2]  # 親のカテゴリを使用

        # アイデアを登録
        con.execute(
            "INSERT INTO ideas (idea_id, title, detail, category, user_id, created_at, inheritance_flag) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (child_idea_id, child_title, add_detail, child_category, user_id, created_at, True)
        )

        # 継承情報を登録
        inheritance_id = str(uuid.uuid4())
        con.execute(
            """
            INSERT INTO idea_inheritance 
            (inheritance_id, parent_idea_id, parent_user_id, child_idea_id, child_user_id, add_point, add_detail, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (inheritance_id, parent_idea_id, parent_user_id, child_idea_id, user_id, add_point, add_detail if add_detail else None, created_at)
        )

    flash('アイデアを継承して新規投稿しました。')
    return redirect(url_for('index'))

@app.route('/inheritance/view/<inheritance_id>')
@login_required
def inheritance_view(inheritance_id):
    user_id = session['user_id']
    
    with get_connection() as con:
        row = con.execute("""
            SELECT 
                ii.inheritance_id,
                ii.parent_idea_id,
                ii.child_idea_id,
                ii.add_point,
                ii.add_detail,
                ii.created_at,
                parent_i.title as parent_title,
                parent_i.detail as parent_detail,
                parent_i.category as parent_category,
                parent_u.nickname as parent_nickname,
                child_i.title as child_title,
                child_i.detail as child_detail,
                child_i.category as child_category
            FROM idea_inheritance ii
            LEFT JOIN ideas parent_i ON ii.parent_idea_id = parent_i.idea_id
            LEFT JOIN mypage parent_u ON ii.parent_user_id = parent_u.user_id
            LEFT JOIN ideas child_i ON ii.child_idea_id = child_i.idea_id
            WHERE ii.inheritance_id = ?
        """, (inheritance_id,)).fetchone()
        
        if not row:
            flash('継承情報が見つかりません。')
            return redirect(url_for('mypage'))
            
        inheritance = {
            'inheritance_id': row[0],
            'parent_idea_id': row[1],
            'child_idea_id': row[2],
            'add_point': row[3],
            'add_detail': row[4],
            'created_at': row[5],
            'parent_title': row[6],
            'parent_detail': row[7],
            'parent_category': row[8],
            'parent_nickname': row[9] if row[9] else '不明なユーザー',
            'child_title': row[10],
            'child_detail': row[11],
            'child_category': row[12]
        }
        
    return render_template('inheritance_view.html', inheritance=inheritance)


@app.route('/post', methods=['POST'])
def post():
    if 'user_id' not in session:
        return redirect(url_for('login', next=url_for('form')))

    title = request.form['title']
    detail = request.form['detail']
    category = request.form['category']

    if calculate_text_length(title) > MAX_TITLE_LENGTH:
        flash(
            f'タイトルは全角{MAX_TITLE_LENGTH // 2}文字（半角{MAX_TITLE_LENGTH}文字）以内で入力してください。'
        )
        return redirect(url_for('form'))

    if calculate_text_length(detail) > MAX_POST_LENGTH:
        flash(
            f'アイデアの詳細は全角{MAX_POST_LENGTH // 2}文字（半角{MAX_POST_LENGTH}文字）以内で入力してください。'
        )
        return redirect(url_for('form'))

    with get_connection() as con:
        idea_id = str(uuid.uuid4())
        user_id = session['user_id']
        created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        con.execute(
            "INSERT INTO ideas VALUES (?, ?, ?, ?, ?, ?)",
            [idea_id, title, detail, category, user_id, created_at]
        )

    return redirect(url_for('index'))


@app.route('/ideas/<idea_id>/delete', methods=['POST'])
@login_required
def delete_idea(idea_id):
    user_id = session['user_id']

    with get_connection() as con:
        cur = con.cursor()
        idea_row = cur.execute(
            "SELECT user_id FROM ideas WHERE idea_id = ?",
            (idea_id,)
        ).fetchone()

        if not idea_row or idea_row[0] != user_id:
            flash('指定した投稿を削除できません。')
            return redirect(url_for('mypage'))

        cur.execute("DELETE FROM gacha_result WHERE idea_id = ?", (idea_id,))
        cur.execute("DELETE FROM ideas WHERE idea_id = ?", (idea_id,))
        con.commit()

    flash('投稿を削除しました。')
    return redirect(url_for('mypage'))


@app.route('/posts/<idea_id>')
@login_required
def post_view(idea_id):
    user_id = session['user_id']

    with get_connection() as con:
        row = con.execute(
            """
            SELECT 
                i.idea_id,
                i.title,
                i.detail,
                i.category,
                i.created_at,
                i.user_id,
                u.nickname,
                u.icon_path,
                i.inheritance_flag
            FROM ideas i
            LEFT JOIN mypage u ON i.user_id = u.user_id
            WHERE i.idea_id = ?
            """,
            (idea_id,)
        ).fetchone()

    if not row:
        flash('投稿が見つかりませんでした。')
        return redirect(url_for('mypage'))

    # 継承されたアイデアの場合は継承詳細画面へリダイレクト
    if row[8]: # inheritance_flag
        with get_connection() as con:
            inheritance_row = con.execute(
                "SELECT inheritance_id FROM idea_inheritance WHERE child_idea_id = ?",
                (idea_id,)
            ).fetchone()
            if inheritance_row:
                return redirect(url_for('inheritance_view', inheritance_id=inheritance_row[0]))

    idea = {
        'idea_id': row[0],
        'title': row[1],
        'detail': row[2],
        'category': row[3],
        'created_at': row[4],
        'user_id': row[5],
        'nickname': row[6],
        'icon_path': row[7],
    }

    return render_template('post_view.html', idea=idea)


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    errors = []

    form_data = {
        'user_id': request.form.get('user_id', '@').strip() if request.method == 'POST' else '@',
        'nickname': request.form.get('nickname', '').strip() if request.method == 'POST' else '',
        'email': request.form.get('email', '').strip() if request.method == 'POST' else ''
    }

    if request.method == 'POST':
        raw_user_id = None
        user_id_input = form_data['user_id']
        nickname = form_data['nickname']
        email = form_data['email']
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        icon_file = request.files.get('icon')

        if not user_id_input:
            errors.append('ユーザーIDを入力してください。')
        elif not user_id_input.startswith('@'):
            errors.append('ユーザーIDは先頭に@を付けて入力してください。')
        elif len(user_id_input) == 1:
            errors.append('ユーザーIDが短すぎます。@の後に文字を入力してください。')
        else:
            raw_user_id = user_id_input[1:].strip()
            if not raw_user_id:
                errors.append('ユーザーIDが短すぎます。@の後に文字を入力してください。')
            elif len(raw_user_id) > 31:
                errors.append('ユーザーIDは31文字以内で入力してください。')
            elif not raw_user_id.replace('_', '').replace('-', '').isalnum():
                errors.append('ユーザーIDは英数字と-_のみ使用できます。')
            else:
                existing_user_id = get_user_by_user_id(raw_user_id)
                if existing_user_id:
                    errors.append('このユーザーIDは既に利用されています。')

        if not nickname:
            errors.append('ニックネームを入力してください。')

        if not email:
            errors.append('メールアドレスを入力してください。')
        elif '@' not in email or '.' not in email:
            errors.append('正しい形式のメールアドレスを入力してください。')

        if not password:
            errors.append('パスワードを入力してください。')
        elif len(password) < 8:
            errors.append('パスワードは8文字以上で入力してください。')
        elif password != confirm_password:
            errors.append('パスワードと確認用パスワードが一致しません。')

        existing_user = get_user_by_email(email) if email else None
        if existing_user:
            errors.append('このメールアドレスは既に登録されています。')

        icon_path = None
        icon_candidate = None
        if icon_file and icon_file.filename:
            filename = secure_filename(icon_file.filename)
            _, ext = os.path.splitext(filename)
            allowed_extensions = {'.png', '.jpg', '.jpeg', '.gif'}
            if ext.lower() not in allowed_extensions:
                errors.append('アイコン画像はPNG/JPG/GIF形式のみアップロードできます。')
            else:
                icon_candidate = (icon_file, ext.lower())

        if not errors:
            if icon_candidate:
                icon_stream, ext = icon_candidate
                icon_path = store_icon_file(icon_stream, ext)

            user_id = raw_user_id if raw_user_id and not errors else None
            password_hash = generate_password_hash(password)
            created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            insert_user(user_id, nickname, password_hash, email, icon_path, created_at)
            session.clear()
            session.permanent = True
            session['user_id'] = user_id
            session['nickname'] = nickname
            session['email'] = email
            session['icon_path'] = icon_path
            return redirect(url_for('index'))

    return render_template(
        'signup.html',
        errors=errors,
        form_data=form_data
    )


@app.route('/login', methods=['GET', 'POST'])
def login():
    errors = []
    form_data = {
        'identifier': request.form.get('identifier', '').strip() if request.method == 'POST' else ''
    }

    next_url = request.args.get('next') or request.form.get('next')

    if request.method == 'POST':
        identifier = form_data['identifier']
        password = request.form.get('password', '')

        if not identifier:
            errors.append('ユーザーIDまたはメールアドレスを入力してください。')

        if not password:
            errors.append('パスワードを入力してください。')

        user_row = None

        if identifier and not errors:
            if identifier.startswith('@'):
                candidate_id = identifier[1:].strip()
                if candidate_id:
                    user_row = get_user_by_user_id(candidate_id)
            else:
                user_row = get_user_by_email(identifier)
                if not user_row:
                    candidate_id = identifier.strip()
                    if candidate_id:
                        user_row = get_user_by_user_id(candidate_id)

            if not user_row:
                errors.append('該当するユーザーが見つかりませんでした。')

        if not errors and user_row:
            stored_hash = user_row[2]
            if not check_password_hash(stored_hash, password):
                errors.append('ユーザーIDまたはパスワードが正しくありません。')

        if not errors and user_row:
            session.clear()
            session.permanent = True
            session['user_id'] = user_row[0]
            session['nickname'] = user_row[1]
            session['email'] = user_row[3]
            session['icon_path'] = user_row[4]

            if next_url:
                return redirect(next_url)
            return redirect(url_for('index'))

    return render_template(
        'login.html',
        errors=errors,
        form_data=form_data,
        next_url=next_url
    )


@app.route('/logout', methods=['POST'])
@login_required
def logout():
    session.clear()
    return redirect(url_for('index'))

# ここからガチャ機能
@app.route('/gacha')
@login_required
def gacha():
    selected_category = request.args.get("category", "")
    return render_template("gacha.html", selected_category=selected_category)

# ランダムに1つのアイテムを表示するルート
@app.route('/result')
@login_required
def result():
    idea = None
    idea_id = session.pop('last_gacha_idea_id', None)

    if idea_id:
        with get_connection() as con:
            idea = con.execute(
                "SELECT idea_id, title, detail, category, user_id, created_at FROM ideas WHERE idea_id = ?",
                (idea_id,)
            ).fetchone()

    return render_template("result.html", item=idea)

# ガチャを回して結果ページにリダイレクトするルート
@app.route('/spin')
@login_required
def spin():
    current_user_id = session.get('user_id')
    category = request.args.get('category')  # 💡カテゴリを取得

    item = fetch_random_item(
        exclude_user_id=current_user_id,
        category=category
    )

    if not item:
        session['last_gacha_idea_id'] = None
        flash('現在引けるアイデアがありません。')
        return redirect(url_for('result', category=category))

    idea_id = item[0]
    author_id = item[4]
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    with get_connection() as con:
        con.execute(
            "INSERT INTO gacha_result (result_id, user_id, idea_id, created_at) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), current_user_id, idea_id, now)
        )
        if author_id and author_id != current_user_id:
            con.execute(
                "INSERT INTO revival_notify (notify_id, idea_id, author_id, picker_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), idea_id, author_id, current_user_id, now)
            )
        con.commit()

    session['last_gacha_idea_id'] = idea_id

    # ✅ カテゴリをつけて結果ページにリダイレクト
    return redirect(url_for('result', category=category))

# ここまでガチャ機能

# マイページ
@app.route('/mypage/update', methods=['POST'])
def update_profile():
    user_id = get_current_user_id()

    nickname = request.form.get('nickname', '').strip()
    remove_icon = request.form.get('remove_icon') == '1'
    icon_file = request.files.get('icon')

    errors = []

    if not nickname:
        errors.append('ニックネームを入力してください。')
    elif len(nickname) > MAX_NICKNAME_LENGTH:
        errors.append(f'ニックネームは{MAX_NICKNAME_LENGTH}文字以内で入力してください。')

    icon_candidate = None
    if icon_file and icon_file.filename:
        filename = secure_filename(icon_file.filename)
        _, ext = os.path.splitext(filename)
        ext = ext.lower()
        if ext not in ALLOWED_ICON_EXTENSIONS:
            errors.append('アイコン画像はPNG/JPG/GIF形式のみアップロードできます。')
        else:
            icon_candidate = (icon_file, ext)

    if errors:
        for message in errors:
            flash(message)
        return redirect(url_for('mypage'))

    with get_connection() as con:
        cur = con.cursor()
        current_row = cur.execute(
            "SELECT icon_path FROM mypage WHERE user_id = ?",
            (user_id,)
        ).fetchone()

        if not current_row:
            flash('ユーザー情報が見つかりません。')
            return redirect(url_for('mypage'))

        current_icon_path = current_row[0]
        new_icon_path = current_icon_path

        if icon_candidate:
            new_icon_path = store_icon_file(icon_candidate[0], icon_candidate[1])
        elif remove_icon:
            new_icon_path = None

        cur.execute(
            "UPDATE mypage SET nickname = ?, icon_path = ? WHERE user_id = ?",
            (nickname, new_icon_path, user_id)
        )
        con.commit()

    if (icon_candidate or remove_icon) and current_icon_path and current_icon_path != new_icon_path:
        delete_icon_file(current_icon_path)

    flash('プロフィールを更新しました。')
    return redirect(url_for('mypage'))


@app.route('/mypage')
@login_required
def mypage():
    user_id = session['user_id']

    with get_connection() as con:
        user_row = con.execute(
            "SELECT user_id, nickname, email, icon_path, created_at FROM mypage WHERE user_id = ?",
            (user_id,)
        ).fetchone()

        if not user_row:
            session.clear()
            return redirect(url_for('login'))

        user = {
            'user_id': user_row[0],
            'nickname': user_row[1],
            'email': user_row[2],
            'icon_path': user_row[3],
            'created_at': user_row[4]
        }

        idea_rows = con.execute(
            "SELECT idea_id, title, detail, category, created_at FROM ideas WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)
        ).fetchall()

        gacha_rows = con.execute("""
            SELECT gr.result_id, gr.created_at, i.idea_id, i.title, i.detail, i.category
            FROM gacha_result gr
            JOIN ideas i ON gr.idea_id = i.idea_id
            WHERE gr.user_id = ?
            ORDER BY gr.created_at DESC
        """, (user_id,)).fetchall()

        revival_rows = con.execute("""
            SELECT 
                rn.notify_id,
                rn.created_at,
                rn.picker_id,
                picker.nickname,
                picker.icon_path,
                i.title,
                i.category
            FROM revival_notify rn
            JOIN ideas i ON rn.idea_id = i.idea_id
            LEFT JOIN mypage picker ON rn.picker_id = picker.user_id
            WHERE rn.author_id = ?
            ORDER BY rn.created_at DESC
        """, (user_id,)).fetchall()

        inheritance_rows = con.execute("""
            SELECT 
                ii.inheritance_id,
                ii.parent_idea_id,
                ii.child_idea_id,
                ii.add_point,
                ii.add_detail,
                ii.created_at,
                parent_i.title as parent_title,
                parent_i.detail as parent_detail,
                parent_i.category as parent_category,
                parent_u.nickname as parent_nickname,
                child_i.title as child_title,
                child_i.detail as child_detail,
                child_i.category as child_category
            FROM idea_inheritance ii
            LEFT JOIN ideas parent_i ON ii.parent_idea_id = parent_i.idea_id
            LEFT JOIN mypage parent_u ON ii.parent_user_id = parent_u.user_id
            LEFT JOIN ideas child_i ON ii.child_idea_id = child_i.idea_id
            WHERE ii.child_user_id = ?
            ORDER BY ii.created_at DESC
        """, (user_id,)).fetchall()

    ideas = []
    for row in idea_rows:
        ideas.append({
            'idea_id': row[0],
            'title': row[1],
            'detail': row[2],
            'category': row[3],
            'created_at': row[4]
        })

    gacha_results = []
    for row in gacha_rows:
        gacha_results.append({
            'result_id': row[0],
            'created_at': row[1],
            'idea_id': row[2],
            'idea_title': row[3],
            'detail': row[4],
            'category': row[5]
        })

    revival_notifications = []
    for row in revival_rows:
        revival_notifications.append({
            'notify_id': row[0],
            'created_at': row[1],
            'picker_id': row[2],
            'picker_nickname': row[3] if row[3] else '不明なユーザー',
            'picker_icon_path': row[4],
            'idea_title': row[5],
            'category': row[6]
        })

    inheritance_items = []
    for row in inheritance_rows:
        inheritance_items.append({
            'inheritance_id': row[0],
            'parent_idea_id': row[1],
            'child_idea_id': row[2],
            'add_point': row[3],
            'add_detail': row[4],
            'created_at': row[5],
            'parent_title': row[6],
            'parent_detail': row[7],
            'parent_category': row[8],
            'parent_nickname': row[9] if row[9] else '不明なユーザー',
            'child_title': row[10],
            'child_detail': row[11],
            'child_category': row[12]
        })

    return render_template(
        'mypage.html',
        user=user,
        ideas=ideas,
        gacha_results=gacha_results,
        revival_notifications=revival_notifications,
        inheritance_items=inheritance_items
    )
