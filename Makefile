PY ?= python3
HANDS ?= 4000

.PHONY: test sim gauntlet ablation bot smoke tables clean

test:            ## unit tests
	$(PY) -m pytest -q

sim:             ## one 8-bot match with the event-day bot
	$(PY) -m sim.run safe tag lag call nit potodds allin riverrat --hands $(HANDS)

gauntlet:        ## model bot vs every zoo field
	$(PY) -m sim.gauntlet model --vs main --hands $(HANDS)

ablation:
	$(PY) -m sim.ablation --hands $(HANDS)

bot:             ## bundle the submission into dist/bot and smoke-test it through the fake dealer
	$(PY) scripts/bundle.py --out dist/bot
	$(MAKE) smoke

smoke:
	$(PY) -m adapter.fake_dealer --cmd "$(PY) dist/bot/main.py" --hands 200 --minimal
	$(PY) -m adapter.fake_dealer --cmd "$(PY) dist/bot/main.py" --hands 200

tables:          ## rebuild the preflop equity table (~1 min)
	$(PY) -m core.preflop --samples 20000

clean:
	rm -rf dist logs .pytest_cache
