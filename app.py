import os
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory
from werkzeug.utils import secure_filename
from moviepy.editor import ImageSequenceClip, AudioFileClip, concatenate_videoclips, ColorClip, CompositeVideoClip
from PIL import Image, ImageFont, ImageDraw, ImageFilter
from enum import Enum

app = Flask(__name__)
app.secret_key = 'your_secret_key'

# Конфигурация
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['OUTPUT_FOLDER'] = 'static/output'
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'mp3', 'wav'}
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB

# Настройки по умолчанию
DEFAULT_OUTPUT = "output.mp4"
DEFAULT_DURATION = 4
DEFAULT_FPS = 30
BLUR_RADIUS = 20
TRANSITION_DURATION = 0.5
OUTPUT_SIZE = (1080, 1920)


class TransitionType(Enum):
    FADE = 1
    SLIDE_RIGHT = 2
    SLIDE_DOWN = 3


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']


def create_cover(image_path, custom_text="Моя обложка"):
    img = Image.open(image_path)
    img.thumbnail((OUTPUT_SIZE[0], OUTPUT_SIZE[1]), Image.LANCZOS)

    bg = img.copy().resize(OUTPUT_SIZE).filter(ImageFilter.GaussianBlur(BLUR_RADIUS))
    bg.paste(img, ((OUTPUT_SIZE[0] - img.width) // 2, (OUTPUT_SIZE[1] - img.height) // 2))

    if custom_text:
        try:
            draw = ImageDraw.Draw(bg)
            font = ImageFont.truetype("arial.ttf", 60)
            shadow_color = (0, 0, 0, 150)
            for i in [(x, y) for x in (-2, 0, 2) for y in (-2, 0, 2) if x or y]:
                draw.text((50 + i[0], 50 + i[1]), custom_text, fill=shadow_color, font=font)
            draw.text((50, 50), custom_text, fill=(255, 255, 255), font=font)
        except:
            font = ImageFont.load_default()
            draw.text((50, 50), custom_text, fill=(255, 255, 255), font=font)

    cover_path = os.path.join(app.config['UPLOAD_FOLDER'], "cover.jpg")
    bg.save(cover_path)
    return cover_path


def process_image(image_path):
    img = Image.open(image_path)
    width, height = img.size
    target_width, target_height = OUTPUT_SIZE
    img_ratio = width / height
    target_ratio = target_width / target_height

    if img_ratio > target_ratio:
        new_width = target_width
        new_height = int(target_width / img_ratio)
    else:
        new_height = target_height
        new_width = int(target_height * img_ratio)

    img = img.resize((new_width, new_height), Image.LANCZOS)
    bg = img.copy().resize(OUTPUT_SIZE, Image.LANCZOS).filter(ImageFilter.GaussianBlur(BLUR_RADIUS))
    position = ((target_width - new_width) // 2, (target_height - new_height) // 2)
    bg.paste(img, position)

    temp_path = os.path.join(app.config['UPLOAD_FOLDER'], f"processed_{os.path.basename(image_path)}")
    bg.save(temp_path)
    return temp_path


def apply_transition(clip1, clip2, transition_type):
    duration = TRANSITION_DURATION

    if transition_type == TransitionType.FADE:
        return concatenate_videoclips([clip1, clip2], method="compose", transition=clip1.duration / 2)

    elif transition_type == TransitionType.SLIDE_RIGHT:
        def slide_right(t):
            if t < duration:
                return (min(0, -clip2.w + int((t / duration) * clip2.w)), 'center')
            return ('center', 'center')

        moving_clip = clip2.set_position(slide_right)
        return CompositeVideoClip([
            clip1.set_end(clip1.duration),
            moving_clip.set_start(clip1.duration - duration)
        ]).set_duration(clip1.duration + clip2.duration - duration)

    elif transition_type == TransitionType.SLIDE_DOWN:
        def slide_down(t):
            if t < duration:
                return ('center', min(0, -clip2.h + int((t / duration) * clip2.h)))
            return ('center', 'center')

        moving_clip = clip2.set_position(slide_down)
        return CompositeVideoClip([
            clip1.set_end(clip1.duration),
            moving_clip.set_start(clip1.duration - duration)
        ]).set_duration(clip1.duration + clip2.duration - duration)

def create_video(images, audio_path, output_filename, transition_type, duration, fps):
    # Создаем обложку
    if images:
        cover_path = create_cover(images[0], request.form.get('cover_text', 'Моя обложка'))
        images = [cover_path] + images

    # Обрабатываем изображения
    processed_images = []
    for img_path in images:
        try:
            processed_path = process_image(img_path)
            processed_images.append(processed_path)
        except Exception as e:
            flash(f"Ошибка обработки {img_path}: {e}", 'error')

    if not processed_images:
        raise ValueError("Нет изображений для создания видео")

    # Создаем клипы
    clips = [ImageSequenceClip([img], durations=[duration]) for img in processed_images]

    # Применяем переходы
    final_clip = clips[0]
    for next_clip in clips[1:]:
        final_clip = apply_transition(final_clip, next_clip, transition_type)

    # Обрабатываем аудио
    if audio_path:
        audio_clip = AudioFileClip(audio_path)
        if audio_clip.duration > final_clip.duration:
            audio_clip = audio_clip.subclip(0, final_clip.duration)
        else:
            # Зацикливаем аудио, если оно короче видео
            audio_clip = audio_clip.audio_loop(duration=final_clip.duration)
        final_clip = final_clip.set_audio(audio_clip)

    # Сохраняем видео
    output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)
    final_clip.write_videofile(
        output_path,
        fps=fps,
        codec='libx264',
        audio_codec='aac',
        threads=4,
        preset='fast'
    )

    # Удаляем временные файлы
    for img_path in processed_images + [audio_path]:
        try:
            if os.path.exists(img_path):
                os.remove(img_path)
        except Exception as ex:
            print(f"Не удалось удалить {img_path}: {ex}")

    return output_path

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        if 'images' not in request.files or 'audio' not in request.files:
            flash('Пожалуйста, загрузите все файлы', 'error')
            return redirect(request.url)

        images = request.files.getlist('images')
        audio = request.files['audio']

        if not images or images[0].filename == '' or audio.filename == '':
            flash('Выберите хотя бы одно изображение и аудиофайл', 'error')
            return redirect(request.url)

        image_paths = []
        for image in images:
            if image and allowed_file(image.filename):
                filename = secure_filename(image.filename)
                save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                image.save(save_path)
                image_paths.append(save_path)
            else:
                flash('Недопустимый формат изображения', 'error')
                return redirect(request.url)

        if audio and allowed_file(audio.filename):
            audio_filename = secure_filename(audio.filename)
            audio_path = os.path.join(app.config['UPLOAD_FOLDER'], audio_filename)
            audio.save(audio_path)
        else:
            flash('Недопустимый формат аудиофайла', 'error')
            return redirect(request.url)

        try:
            output_filename = request.form.get('output_filename', DEFAULT_OUTPUT)
            duration = float(request.form.get('duration', DEFAULT_DURATION))
            fps = int(request.form.get('fps', DEFAULT_FPS))
            transition_type = TransitionType(int(request.form.get('transition', 1)))
        except ValueError:
            flash('Некорректные параметры видео', 'error')
            return redirect(request.url)

        try:
            video_path = create_video(image_paths, audio_path, output_filename, transition_type, duration, fps)
            return redirect(url_for('result', filename=output_filename))
        except Exception as e:
            flash(f'Ошибка при создании видео: {str(e)}', 'error')
            return redirect(request.url)

    return render_template('index.html')

@app.route('/result/<filename>')
def result(filename):
    if not os.path.exists(os.path.join(app.config['OUTPUT_FOLDER'], filename)):
        flash('Видео не найдено', 'error')
        return redirect(url_for('index'))
    return render_template('result.html', video_filename=filename)

@app.route('/download/<filename>')
def download(filename):
    return send_from_directory(app.config['OUTPUT_FOLDER'], filename, as_attachment=True)

if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)
    app.run(debug=True)