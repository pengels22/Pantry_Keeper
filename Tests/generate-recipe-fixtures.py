"""Generate iOS response fixtures from the bundled backend on an isolated test DB.
Run with the backend's Python dependencies installed, from the repository root.
"""
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
backend = root / 'Backend' / 'Pantry_Keeper'
sys.path[:0] = [str(backend / 'tests'), str(backend)]
from test_recipes import RecipeTests

out = root / 'Tests' / 'Fixtures'
out.mkdir(exist_ok=True)
def write(name, data):
    (out / (name + '.json')).write_text(json.dumps(data, indent=2) + '\n')

test = RecipeTests()
test.setUp()
write('inventory', test.client.get('/api/inventory').json())
write('inventory-item', test.client.get(f'/api/inventory/{test.ids[0]}').json())
proposal = test.client.post('/api/recipes/propose', json=test.proposal)
assert proposal.status_code == 200, proposal.text
write('proposal', proposal.json())
write('chat', {'reply': 'Try chicken pasta with ingredients from your pantry.', 'proposal': proposal.json()})
selected = test.select()
write('selected', test.client.get(f'/api/recipes/{selected}').json())
write('reserved', test.reserve(selected))
completed = test.client.post(f'/api/recipes/{selected}/commit', json={'confirmed': True, 'actual_usage': test.usage((0, 7, 2))})
assert completed.status_code == 200, completed.text
write('completed', completed.json())
cancelled = test.select()
response = test.client.post(f'/api/recipes/{cancelled}/cancel')
assert response.status_code == 200, response.text
write('cancelled', response.json())
write('sessions', test.client.get('/api/recipes/sessions').json())
print('Generated fixtures from isolated backend responses.')
