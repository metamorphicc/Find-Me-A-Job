from job_search_automation.config import SearchConfig
from job_search_automation.superjob import SuperJobClient, vacancy_from_superjob


class Response:
    def raise_for_status(self):
        pass

    def json(self):
        return {
            "objects": [
                {
                    "id": 123,
                    "link": "https://www.superjob.ru/vakansii/python-123.html",
                    "profession": "Python developer",
                    "firm_name": "Example",
                    "town": {"title": "Москва"},
                    "place_of_work": {"title": "Удалённая работа"},
                    "date_published": 1780000000,
                    "payment_from": 90000,
                    "currency": "RUR",
                    "candidat": "Опыт Python",
                }
            ]
        }


class Session:
    def __init__(self):
        self.headers = None

    def get(self, _url, *, params, headers, timeout):
        self.headers = headers
        assert params["keyword"] == "Python"
        assert timeout == 25
        return Response()


def test_superjob_requires_key_and_maps_explicit_remote():
    session = Session()
    settings = SearchConfig(("Python",), (), (), (), True, True, 7, 20)
    items = SuperJobClient("secret", session).search(settings)
    assert session.headers == {"X-Api-App-Id": "secret"}
    assert items[0].is_remote()
    assert items[0].salary_from == 90000


def test_superjob_does_not_assume_remote_from_city():
    item = Response().json()["objects"][0]
    item["place_of_work"] = {"title": "На территории работодателя"}
    vacancy = vacancy_from_superjob(item, "Python")
    assert vacancy is not None
    assert not vacancy.is_remote()
