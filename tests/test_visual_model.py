from pathlib import Path
from tempfile import TemporaryDirectory

from deltarune_agent.visual_model import OnlineVisualModel


def test_online_visual_prototypes_learn_and_persist():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "visual.json"
        model = OnlineVisualModel(path)
        for offset in range(6):
            delta = offset * 0.001
            model.update("overworld", (0.2 + delta, 0.3, 0.4))
            model.update("battle", (0.8 + delta, 0.7, 0.6))
        prediction = model.predict((0.805, 0.7, 0.6))
        assert prediction is not None and prediction[0] == "battle"

        model.save()
        reloaded = OnlineVisualModel.load(path)
        prediction = reloaded.predict((0.205, 0.3, 0.4))

        assert prediction is not None and prediction[0] == "overworld"
