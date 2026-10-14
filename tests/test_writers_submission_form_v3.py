"""Writers Mini App v3: genres, content notices, sketches, copy-ready posts."""
from __future__ import annotations

import unittest

from writers_submission.models import ModerationDeliveryContext, ValidationError
from writers_submission.promo import quote_work_title, render_owner_promo
from writers_submission.delivery import _moderation_text
from writers_submission.uploads import (
    normalize_submission_details, validate_description_for_format, count_description_sentences,
    validate_submission_fields,
)


DESCRIPTION = "Сначала герой получает письмо. Затем события меняют привычную жизнь. В финале он делает важный выбор."


def common_details(**updates):
    details = {
        "form_version": 3,
        "fandom": "Шерлок",
        "size_category": "миди",
        "rating": "R",
        "completion": "в процессе",
        "visual_mode": "palette",
        "palette_colors": ["#AABBCC", "#112233", "#445566", "#778899"],
        "extra_links": [],
        "main_genres": ["Романтика", "Детектив", "Путешествие во времени"],
        "warnings_present": "yes",
        "warnings": ["Насилие", "Психологические травмы"],
    }
    details.update(updates)
    return details


def full_submission(details=None, **updates):
    fields = {
        "title": "Рассказ о городе",
        "work_type": "ФФ",
        "genre": "Гет",
        "description": DESCRIPTION,
        "body_text": "",
        "external_url": "https://ficbook.net/readfic/12345",
        "has_ready_file": False,
        "details": details if details is not None else common_details(),
    }
    fields.update(updates)
    return validate_submission_fields(**fields)


class NewSubmissionFormTests(unittest.TestCase):
    def test_three_unique_genres_and_content_warnings_are_mandatory(self):
        fields = full_submission()
        self.assertEqual(len(fields.details["main_genres"]), 3)
        self.assertEqual(fields.details["warnings_present"], "yes")
        self.assertEqual(len(fields.details["warnings"]), 2)
        with self.assertRaisesRegex(ValidationError, "main_genres"):
            full_submission(common_details(main_genres=["Раз", "Два", "Три", "Четыре"]))
        with self.assertRaisesRegex(ValidationError, "repeat"):
            full_submission(common_details(main_genres=["Романтика", "романтика"]))
        with self.assertRaisesRegex(ValidationError, "main genre"):
            full_submission(common_details(main_genres=[]))
        with self.assertRaisesRegex(ValidationError, "yes or no"):
            full_submission(common_details(warnings_present="", warnings=[]))
        with self.assertRaisesRegex(ValidationError, "content warning"):
            full_submission(common_details(warnings=[]))
        with self.assertRaisesRegex(ValidationError, "warnings require"):
            full_submission(common_details(warnings_present="no", warnings=["Насилие"]))

    def test_no_warning_requires_explicit_no_but_no_text(self):
        fields = full_submission(common_details(warnings_present="no", warnings=[]))
        self.assertEqual(fields.details["warnings"], [])
        post = render_owner_promo(ModerationDeliveryContext(
            author_user_id=10, title=fields.title, work_type=fields.work_type,
            genre=fields.genre, description=fields.description,
            body_text="", external_url=fields.external_url, files=(),
            details=fields.details,
        ))
        self.assertIn("• Романтика • Детектив • Путешествие во времени •", post)
        self.assertNotIn("ПРЕДУПРЕЖДЕНИЯ", post)

    def test_long_description_between_three_sentences_and_110_words(self):
        valid = DESCRIPTION
        self.assertLessEqual(len(valid.split()), 110)
        validate_description_for_format(valid, "ФФ")
        with self.assertRaisesRegex(ValidationError, "three sentences"):
            full_submission(description="Это пока только одна фраза.")
        with self.assertRaisesRegex(ValidationError, "three sentences"):
            full_submission(description="Начало. Середина. Конец")
        with self.assertRaisesRegex(ValidationError, "110 words"):
            full_submission(description=" ".join(["слово"] * 111) + ". Потом. Конец.")
        long_but_valid = " ".join(["длинное"] * 100) + ". Еще предложение. Последнее предложение."
        validate_description_for_format(long_but_valid, "Оридж")

    def test_completed_sentences_survive_trailing_tags_emoji_and_fragment(self):
        good = [
            "Это первая глава. У героя появляется цель! Дальше будет интереснее? #фанфик",
            "Первая сцена окончена. Вторая сцена начинается. Третья сцена описана. Продолжение позже",
            "Все получилось. Все стали друзьями. Сюжет завершен. ❤️ #романтика",
            "История начинается. Дальше начинается путешествие. И все меняется. #фикбук #фэнтези",
            "Далеко отсюда. Уже наступило утро. Герои все поняли. https://ficbook.net/readfic/123",
        ]
        for text in good:
            with self.subTest(text=text):
                self.assertGreaterEqual(count_description_sentences(text), 3)
                validate_description_for_format(text, "ФФ")
        for text in ("Начало главы. Середина главы. Концовка без точки #хештег",
                     "Один текст. Еще одно предложение. #тег"):
            with self.subTest(text=text):
                self.assertLess(count_description_sentences(text), 3)
                with self.assertRaisesRegex(ValidationError, "three sentences"):
                    validate_description_for_format(text, "ФФ")

    def test_warning_and_genres_are_escaped_and_positioned_after_description(self):
        details = common_details(
            main_genres=["Романтика", "Фэнтези & сказки"],
            warnings=["Насилие", "<Спойлер>"],
        )
        fields = full_submission(details)
        post = render_owner_promo(ModerationDeliveryContext(
            author_user_id=10, title=fields.title, work_type=fields.work_type,
            genre=fields.genre, description=fields.description,
            body_text="", external_url=fields.external_url, files=(),
            details=fields.details,
        ))
        self.assertLess(post.index(DESCRIPTION), post.index("• Романтика"))
        self.assertLess(post.index("• Романтика"), post.index("ПРЕДУПРЕЖДЕНИЯ"))
        self.assertIn("🚨 <b>ПРЕДУПРЕЖДЕНИЯ (18+)</b>", post)
        self.assertIn(
            "<tg-spoiler>Насилие × &lt;Спойлер&gt;</tg-spoiler>", post
        )
        self.assertLess(post.index("ПРЕДУПРЕЖДЕНИЯ"), post.index("<tg-spoiler>"))
        self.assertNotIn("🚨 <tg-spoiler>", post)
        self.assertIn("Фэнтези &amp; сказки", post)
        self.assertNotIn("<Спойлер>", post)

    def test_titles_use_one_pair_of_russian_guillemets(self):
        for original in (
            "Название", '"Название"', "«Название»", "“Название”",
            "„Название“", "'Название'", '«"Название"»',
            "  « Название »  ",
        ):
            with self.subTest(original=original):
                self.assertEqual(quote_work_title(original), "«Название»")
        self.assertEqual(quote_work_title(""), "«Название»")
        self.assertEqual(
            quote_work_title("Название \"с кавычками\" внутри"),
            '«Название "с кавычками" внутри»',
        )

    def test_approved_post_and_moderation_card_quote_titles_and_spoil_warnings(self):
        for entered_title in ("Название", "«Название»", '"Название"'):
            context = ModerationDeliveryContext(
                author_user_id=10, title=entered_title, work_type="ФФ",
                genre="Гет", description=DESCRIPTION, body_text="",
                external_url="https://ficbook.net/readfic/12345", files=(),
                details=common_details(warnings=[
                    "Буллинг", " буллинг ", "<Опасный текст> & другое", "",
                ]),
            )
            with self.subTest(title=entered_title):
                post = render_owner_promo(context)
                moderation = _moderation_text(context)
                self.assertIn("<b>«Название»</b>", post)
                self.assertIn("Название: «Название»", moderation)
                self.assertNotIn("««Название»»", post + moderation)
                self.assertEqual(post.count("<tg-spoiler>"), 1)
                self.assertIn(
                    "<tg-spoiler>Буллинг × &lt;Опасный текст&gt; &amp; другое</tg-spoiler>",
                    post,
                )
                self.assertIn("<tg-spoiler>", moderation)
                self.assertNotIn("<Опасный текст>", post + moderation)

    def test_no_content_warnings_do_not_create_spoiler(self):
        context = ModerationDeliveryContext(
            author_user_id=10, title="Название", work_type="Оридж",
            genre="Джен", description=DESCRIPTION, body_text="",
            external_url="https://ficbook.net/readfic/12345", files=(),
            details=common_details(warnings_present="no", warnings=[]),
        )
        post = render_owner_promo(context)
        self.assertNotIn("ПРЕДУПРЕЖДЕНИЯ", post)
        self.assertNotIn("<tg-spoiler>", post)
        self.assertEqual(quote_work_title(context.title), "«Название»")


    def test_sketch_requires_ficbook_profile_and_telegraph_article(self):
        sketch_details = common_details(
            size_category="", completion="", visual_mode="editorial",
            palette_colors=[], fandom="",
            sketch_origin="Ориджинал",
            sketch_profile_url="https://ficbook.net/authors/1234",
            sketch_article_url="https://telegra.ph/First-Sketch-10-13",
        )
        fields = full_submission(
            details=sketch_details,
            work_type="Зарисовка",
            genre="Слэш",
            description="Странная встреча меняет весь вечер.",
            external_url=sketch_details["sketch_article_url"],
        )
        self.assertEqual(fields.details["visual_mode"], "editorial")
        context = ModerationDeliveryContext(
            author_user_id=10, title=fields.title, work_type=fields.work_type,
            genre=fields.genre, description=fields.description,
            body_text="", external_url=fields.external_url, files=(),
            details=fields.details,
        )
        post = render_owner_promo(context)
        self.assertIn("#Ориджинал", post)
        self.assertIn("#R", post)
        self.assertIn("#Слэш", post)
        self.assertIn("Обложка для рубрики", post)
        self.assertIn("Профиль автора на Фикбуке", post)
        self.assertIn("Читать зарисовку в Telegraph", post)
        self.assertNotIn("Объем:", post)
        self.assertNotIn("цветов", post)
        self.assertLess(post.index(fields.description), post.index("• Романтика"))

        with self.assertRaisesRegex(ValidationError, "sketch_origin"):
            full_submission(details={**sketch_details, "sketch_origin": ""},
                            work_type="Зарисовка", description="Одна встреча.",
                            external_url=sketch_details["sketch_article_url"])
        with self.assertRaisesRegex(ValidationError, "sketch_profile_url"):
            full_submission(details={**sketch_details, "sketch_profile_url": "https://bad.com/author"},
                            work_type="Зарисовка", description="Одна встреча.",
                            external_url=sketch_details["sketch_article_url"])
        with self.assertRaisesRegex(ValidationError, "sketch_article_url"):
            full_submission(details={**sketch_details, "sketch_article_url": "https://example.com/story"},
                            work_type="Зарисовка", description="Одна встреча.",
                            external_url="https://example.com/story")
        with self.assertRaisesRegex(ValidationError, "must match"):
            full_submission(details=sketch_details, work_type="Зарисовка",
                            description="Одна встреча.", external_url="https://ficbook.net/readfic/2")

    def test_sketch_description_stays_within_35_words_and_230_chars(self):
        validate_description_for_format("Ночь меняет все.", "Зарисовка")
        with self.assertRaisesRegex(ValidationError, "35 words"):
            validate_description_for_format(" ".join(["текст"] * 36), "Зарисовка")
        with self.assertRaisesRegex(ValidationError, "230 characters"):
            validate_description_for_format("д" * 231, "Зарисовка")

    def test_autosave_accepts_partial_v3_drafts(self):
        fields = validate_submission_fields(
            title="Идея", work_type="", genre="", description="",
            body_text="", external_url=None, has_ready_file=False,
            details={"form_version": 3, "main_genres": [], "warnings_present": ""},
            require_work_content=False,
        )
        self.assertEqual(fields.title, "Идея")
        self.assertEqual(fields.description, "")

    def test_existing_v2_details_still_parse_for_legacy_posts(self):
        legacy = normalize_submission_details({
            "form_version": 2, "size_category": "миди",
            "rating": "PG-13", "completion": "в процессе",
            "visual_mode": "palette", "palette_colors": [
                "#112233", "#223344", "#334455", "#445566",
            ],
        }, complete=True)
        self.assertEqual(legacy["form_version"], 2)
        self.assertNotIn("main_genres", legacy)


if __name__ == "__main__":
    unittest.main()
