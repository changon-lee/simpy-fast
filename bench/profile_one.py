"""cProfile one workload against a tree: python bench/profile_one.py TREE WORKLOAD [N]"""
import cProfile, os, pstats, sys
tree = os.path.abspath(sys.argv[1]); sys.path.insert(0, tree)
sys.path.insert(1, os.path.dirname(os.path.abspath(__file__)))
import simpy
from workloads import WORKLOADS
pr = cProfile.Profile(); pr.enable()
ops, trace = WORKLOADS[sys.argv[2]](simpy)
pr.disable()
if sys.argv[2] == 'dse_mix': print('dse stats', trace[-1])
st = pstats.Stats(pr); st.sort_stats('tottime')
st.print_stats(int(sys.argv[3]) if len(sys.argv) > 3 else 14)
