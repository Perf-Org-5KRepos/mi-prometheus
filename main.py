import torch
from torch import nn
from data_gen.build_data_gen_v0 import init_state, build_data_distraction
from ntm.ntm_layer import NTM
import numpy as np
import torch.cuda as cuda

torch.set_num_threads(1)
CUDA = False
# set seed
torch.manual_seed(2)
np.random.seed(200)
if CUDA:
    torch.cuda.manual_seed(2)

# data_gen generator x,y
batch_size = 1
min_len = 1
max_len = 10
bias = 0.5
element_size = 8
nb_markers_max = 4
nb_markers_min = 1

# init state, memory, attention
tm_in_dim = element_size + 3
tm_output_units = element_size
tm_state_units = 5
n_heads = 1
M = 10
is_cam = False
num_shift = 3

# To be saved for testing
args_save = {'tm_in_dim': tm_in_dim, 'tm_output_units': tm_output_units, 'tm_state_units': tm_state_units
             , 'n_heads': n_heads, 'is_cam': is_cam, 'num_shift': num_shift, 'M': M, 'element_size': element_size}

# Instantiate
ntm = NTM(tm_in_dim, tm_output_units, tm_state_units, n_heads, is_cam, num_shift, M)
if CUDA:
    ntm.cuda()

# Set loss and optimizer
criterion = nn.BCELoss()
optimizer = torch.optim.Adam(ntm.parameters(), lr=0.01)
#optimizer = torch.optim.RMSprop(ntm.parameters(), lr=0.01, momentum=0.9, alpha=0.95)

valid_step = False
if valid_step:
    # generate data for validation
    _, states_valid = init_state(batch_size, tm_output_units, tm_state_units, n_heads, 100, M)
    data_gen = build_data_distraction(20, 20, batch_size, bias, element_size, 5, 6)
    for inputs, targets, nb_marker, mask in data_gen:
        inputs_valid = inputs
        targets_valid = targets
        nb_markers_valid = nb_marker
        mask_valid = mask
        break

# Start Training
epoch = 0
debug = 10000
valid = 100
debug_active = 0
# Data generator : input & target
data_gen = build_data_distraction(min_len, max_len, batch_size, bias, element_size, nb_markers_min, nb_markers_max)
for inputs, targets, nb_marker, mask in data_gen:

    # Init state, memory, attention
    N = 60# max(seq_length) + 1
    _, states = init_state(batch_size, tm_output_units, tm_state_units, n_heads, N, M)

    optimizer.zero_grad()

    output, states_test = ntm(inputs, states, states[1])

    loss = criterion(output[:, mask, :], targets)

    print(", epoch: %d, loss: %1.5f, N %d " % (epoch + 1, loss, N), "nb_marker:", nb_marker)

    loss.backward()
    optimizer.step()

    if (nb_marker == 3 and (loss < 1e-5)) or epoch == 8000:
        path = "./Models/"
        # save model parameters
        torch.save(ntm.state_dict(), path+"model_parameters")
        # save initial arguments of ntm
        np.save(path + 'ntm_arguments', args_save)
        break

    if not(epoch % valid) and epoch != 0 and valid_step:
        # test accuracy
        output, states_test = ntm(inputs_valid, states_valid, states_valid[1])
        output = torch.round(output[:, mask_valid, :])
        acc = 1 - torch.abs(output - targets_valid)
        accuracy = acc.mean()
        print("Accuracy: %.6f" % (accuracy * 100) + "%")
        print("nb markers valid", nb_markers_valid)

    epoch += 1

print("Learning finished!")




