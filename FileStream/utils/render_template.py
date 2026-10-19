import jinja2
from FileStream.config import Telegram, Server
from FileStream.utils.database import Database
from FileStream.utils.human_readable import humanbytes
from FileStream.utils.stream_link import stream_url

db = Database(Telegram.DATABASE_URL, Telegram.SESSION_NAME)

async def render_page(db_id):
    file_data = await db.get_file(db_id)
    # The page itself is rendered from the database, but every subsequent
    # request made by the player uses this self-contained URL instead.
    file_url = stream_url(file_data)
    file_size = humanbytes(int(file_data.get('file_size') or 0))
    file_name = file_data['file_name'].replace("_", " ")

    if str((file_data['mime_type']).split('/')[0].strip()) == 'video':
        template_file = "FileStream/template/play.html"
    else:
        template_file = "FileStream/template/dl.html"

    with open(template_file) as f:
        template = jinja2.Template(f.read())

    return template.render(
        file_name=file_name,
        file_url=file_url,
        file_size=file_size
    )
