#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Copyright (C) IBM Corporation 2018
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""
Defines the COG model

See the reference paper here: https://arxiv.org/abs/1803.06092
"""

__author__ = "Emre Sevgen"

import torch
import numpy as np
import torch.nn as nn
from miprometheus.models.cog.vstm import VSTM
from miprometheus.models.cog.ops import FeatureAttention, SpatialAttention, SemanticAttention
from miprometheus.models.model import Model

class CogModel(Model):

	"""
	``Cog`` is a model for VQA on sequences, using an externally-gates 2D-LSTM-like structure as
	memory, and relying on attention mechanisms. The module consists of a semantic processing
	subunit, a visual processing subunit, a visual memory and a controller. Additionally, the
	controller is allowed to 'ponder' on each sequence element for a variable number of steps
	before moving onto the next input in the sequence. 

	"""

	# Temporary default value for params
	def __init__(self, params, problem_default_values_={}):
		"""
		Constructor of the ``CogModel``. Instantiates all subunits.

		:param params: dictionary of parameters (read from the ``.yaml`` configuration file.)
		:type params: utils.ParamInterface

		:param problem_default_values_: default values coming from the ``Problem`` class.
		:type problem_default_values_: dict

		"""
		# Call base class initialization.
		super(CogModel,self).__init__(params,problem_default_values_)


		self.name = 'CogModel'

		self.data_definitions = {'images': {'size': [-1,-1,-1,-1,-1], 'type': [torch.Tensor]},
														'questions': {'size': [-1,-1], 'type': [torch.Tensor]},
														'targets_class': {'size': [-1,-1,-1], 'type': [torch.Tensor]},
														'targets_reg': {'size' : [-1,-1,2], 'type': [torch.Tensor]}
														}

		# Parameters relating to semantic processing
		# Each word in a given input is first mapped (optionally) to unique integers in a lookup table
		# Then, the resulting ints are embedded using torch nn.Embedding and fed into a bidirectional
		# LSTM. 
		#
		# Input is post-lookup table questions.
		# Output is fed into Semantic Attention mechanism.
		#-----------------------------------------------------------------
		# Initialize lookup table
		self.word_lookup = {}

		# This should be the length of the longest sentence encounterable
		self.nwords = 24
		
		# Length of vectoral representation of each word.
		self.words_embed_length = 64

		# Maximum number of embeddable words.
		self.vocabulary_size = 79

		# LSTM input size (redundant?)
		self.lstm_input_size = self.words_embed_length
		
		# LSTM hidden units.
		self.lstm_hidden_units = 64
		#-----------------------------------------------------------------

		
		# Parameters relating to visual processing
		# Visual processing is a four layer CNN with 32, 64, 64 and 128 channels, 3x3
		# kernels and 2x2 MaxPool layers. Last two layers are subject to feature attention, 
		# and last layer is subject to spatial attention.
		#
		# Input is input images.
		# Output is fed into the Controller and into the VSTM.
		#-----------------------------------------------------------------
		# Input Image size
		self.image_size = [112,112]

		# Number of channels in input Image
		self.image_channels = 3

		# CNN number of channels
		self.visual_processing_channels = [32,64,64,128]

		# Normalization
		self.img_norm = 255.0

		#-----------------------------------------------------------------
	

		# Parameters relating to visual memory
		# Visual memory is a 2D externally gated network. See vstm.py for details.
		#
		# Inputs are output of visual processing, and gating from the Controller.
		# Output is retrieved memory into Controller.
		#-----------------------------------------------------------------
		# Visual memory shape. height x width.
		self.vstm_shape = np.array(self.image_size)
		for channel in self.visual_processing_channels:
			self.vstm_shape = np.floor((self.vstm_shape)/2)
		self.vstm_shape = [int(dim) for dim in self.vstm_shape]

		# Input channels to the visual memory. This is the same as the output of the last CNN layer.
		self.vstm_inchannels = self.visual_processing_channels[-1]

		# Output channels from the visual memory.
		self.vstm_outchannels = 3

		# Number of memory maps used to save information. 
		self.vstm_nmaps = 4

		# Number of pointing classes.
		self.nr_pointers = 49

		#-----------------------------------------------------------------


		# Parameters relating to controller
		# Controller processes input from all subunits, gates the visual memory, and produces
		# classification answers after pondering. It is a GRU.
		#
		# Inputs is concatenation of post-attention Semantic output, Visual processing, and VSTM.
		# Output feeds attention mechanisms, and generates a classification.
		#-----------------------------------------------------------------
		self.controller_input_size = self.lstm_hidden_units*2 + 128 + 128

		# Number of GRU units in controller
		self.controller_output_size = 768

		# Number of pondering steps per item in sequence.
		self.pondering_steps = 4

		# Number of possible classes to output.
		self.nr_classes = 55

		# Controller state norm clip
		self.controller_clip = 10000

		#-----------------------------------------------------------------


		# Define each subunit from provided parameters.
		#-----------------------------------------------------------------	
		#VisualProcessing			(self,in_channels,layer_channels,feature_control_len,spatial_control_len,output_shape)
		self.VisualProcessing(self.image_channels, 
													self.visual_processing_channels,
													self.lstm_hidden_units*2, 
													self.controller_output_size*2,
													self.vstm_shape)

		# SemanticProcessing(self,lstm_input,lstm_hidden,control_len):
		self.SemanticProcessing(self.lstm_input_size,
														self.lstm_hidden_units,
														self.controller_output_size*2)

		self.EmbedVocabulary(self.vocabulary_size,
												 self.words_embed_length)

		self.Controller(self.controller_input_size, 
										self.controller_output_size,
										self.nr_classes)

		self.VisualMemory(self.vstm_shape,
											self.vstm_inchannels,
											self.vstm_outchannels,
											self.vstm_nmaps,
											self.controller_output_size*2,
											self.nr_pointers)

		#-----------------------------------------------------------------
	


		# Initial states of RNNs are trainable parameters. Set them here.
		#-----------------------------------------------------------------
		# Get dtype.
		self.dtype = self.app_state.dtype


		self.attention_init = nn.Parameter(torch.randn((1,self.controller_output_size*2),requires_grad=True).type(self.dtype)*0.1)

		self.controller_state_init = nn.Parameter(torch.randn((1,1,self.controller_output_size),requires_grad=True).type(self.dtype)*0.1)

		self.vstm_state_init = nn.Parameter(torch.randn((1,self.vstm_nmaps,self.vstm_shape[0],self.vstm_shape[1]),requires_grad=True).type(self.dtype)*0.1)

		self.lstm_hidden_init = nn.Parameter(torch.randn((2,1,self.lstm_hidden_units))*0.1 )
		self.lstm_cell_init = nn.Parameter(torch.randn((2,1,self.lstm_hidden_units))*0.1 )
		#-----------------------------------------------------------------

		# Initialize weights and biases
		#-----------------------------------------------------------------
		# Visual processing
		nn.init.xavier_uniform_(self.conv1.weight, gain=nn.init.calculate_gain('relu'))
		nn.init.xavier_uniform_(self.conv2.weight, gain=nn.init.calculate_gain('relu'))
		nn.init.xavier_uniform_(self.conv3.weight, gain=nn.init.calculate_gain('relu'))
		nn.init.xavier_uniform_(self.conv4.weight, gain=nn.init.calculate_gain('relu'))

		self.conv1.bias.data.fill_(0.01)
		self.conv2.bias.data.fill_(0.01)
		self.conv3.bias.data.fill_(0.01)
		self.conv4.bias.data.fill_(0.01)

		# Semantic processing
		for name, param in self.lstm1.named_parameters():
			if 'bias' in name:
				nn.init.constant_(param,0.01)
			elif 'weight' in name:
				nn.init.xavier_uniform_(param)

		# Controller
		for name, param in self.controller1.named_parameters():
			if 'bias' in name:
				nn.init.constant_(param,0.01)
			elif 'weight' in name:
				nn.init.xavier_uniform_(param)

		# Output
		nn.init.xavier_uniform_(self.classifier1.weight)
		self.classifier1.bias.data.fill_(0.01)
		
		nn.init.xavier_uniform_(self.pointer1.weight)
		self.pointer1.bias.data.fill_(0.01)
		#-----------------------------------------------------------------

	# For debugging
	torch.set_printoptions(threshold=999999)



	def forward(self, data_dict):
		"""
		Forward pass of the ``CogModel``.

		:param data_dict: dictionary of data with images, questions.

		:return: Tuple with two predictions: batch with answers and batch with pointing actions 
		"""
		# Parse input
		images = data_dict['images'].permute(1,0,2,3,4) / self.img_norm
		questions = data_dict['questions']

		# Process questions
		questions = self.forward_lookup2embed(questions)
		questions, _ = self.lstm1(questions,(
									 					self.lstm_hidden_init.expand(-1,images.size(1),-1).contiguous(),
														self.lstm_cell_init.expand(-1,images.size(1),-1).contiguous() ) )
		
		output_class = torch.zeros((images.size(1),images.size(0) ,self.nr_classes),requires_grad=False).type(self.dtype)
		output_point = torch.zeros((images.size(1),images.size(0),49),requires_grad=False).type(self.dtype)


		for j, image_seq in enumerate(images):

			# Full pass semantic processing
			out_semantic_attn1 = self.semantic_attn1(questions,
																	self.attention_init.expand(images.size(1),-1))

			# Full pass visual processing
			x = self.conv1(image_seq)
			x	= self.maxpool1(x)
			x = nn.functional.relu(self.batchnorm1(x))
			x	= self.conv2(x)
			x	= self.maxpool2(x)
			x = nn.functional.relu(self.batchnorm2(x))
			x = self.conv3(x)
			x	= self.maxpool3(x)
			x = nn.functional.relu(self.batchnorm3(x))
			x, _ = self.feature_attn1(x,out_semantic_attn1)
			x = self.conv4(x)
			#out_conv4 = self.conv4(out_batchnorm3)
			x = self.maxpool4(x)
			out_batchnorm4 = nn.functional.relu(self.batchnorm4(x))
			x, _ = self.feature_attn2(x,out_semantic_attn1)
			x, _ = self.spatial_attn1(x,self.attention_init.expand(images.size(1),-1))
			out_cnn1 = self.cnn_linear1(x.view(-1,self.visual_processing_channels[3]*self.vstm_shape[0]*self.vstm_shape[1]))

			# Full pass visual memory
			x, vstm_state = self.vstm1(x,self.vstm_state_init.expand(images.size(1),-1,-1,-1),self.attention_init.expand(images.size(1),-1),self.dtype)
			x = self.vstm_linear1(x.view(-1,self.vstm_outchannels*self.vstm_shape[0]*self.vstm_shape[1]))

			# Full pass controller
			y = torch.cat((out_semantic_attn1.unsqueeze(1),out_cnn1.unsqueeze(1),x.unsqueeze(1)),-1)
			y, controller_state = self.controller1(y,self.controller_state_init.expand(-1,images.size(1),-1).contiguous())
			#controller_state = torch.clamp(controller_state, max=self.controller_clip)
			attention = torch.cat((y.squeeze(),controller_state.squeeze()),-1)

			for i in range(self.pondering_steps):
				out_semantic_attn1 = self.semantic_attn1(questions,attention)
				x, _ = self.feature_attn2(out_batchnorm4,out_semantic_attn1)
				x, _ = self.spatial_attn1(x,attention)
				out_cnn1 = self.cnn_linear1(x.view(-1,self.visual_processing_channels[3]*self.vstm_shape[0]*self.vstm_shape[1]))
				x, vstm_state = self.vstm1(x,vstm_state,attention,self.dtype)
				x = self.vstm_linear1(x.view(-1,self.vstm_outchannels*self.vstm_shape[0]*self.vstm_shape[1]))
				y = torch.cat((out_semantic_attn1.unsqueeze(1),out_cnn1.unsqueeze(1),x.unsqueeze(1)),-1)
				y, controller_state = self.controller1(y,controller_state)
				controller_state = torch.clamp(controller_state, max=self.controller_clip)
				attention = torch.cat((y.squeeze(),controller_state.squeeze()),-1)

			classification = self.classifier1(y.squeeze())
			pointing = self.pointer1(x.squeeze())
			#pointing = 0

			output_class[:,j,:] = classification
			output_point[:,j,:] = pointing

		# Return tuple with two outputs.
		return (output_class, output_point)		

	def forward_lookup2embed(self,questions):
		"""
		Performs embedding of lookup-table questions with nn.Embedding.

		:param questions: Tensor of questions in lookup format (Ints)

		"""
		
		out_embed=torch.zeros((questions.size(0),self.nwords,self.words_embed_length),requires_grad=False).type(self.dtype)
		for i, sentence in enumerate(questions):
			out_embed[i,:,:] = ( self.Embedding( sentence ))
		
		return out_embed

	# For a single timepoint in a single sample, returns (nwords,128)
	def forward_embed2lstm(self,out_embed):
	
		out_lstm1, (c_n,h_n) = self.lstm1(out_embed)
		return out_lstm1, (c_n,h_n)

	# Visual Processing
	def VisualProcessing(self,in_channels,layer_channels,feature_control_len,spatial_control_len,output_shape):
		"""
		Defines all layers pertaining to visual processing.

		:param in_channels: Number of channels in images in dataset. Usually 3 (RGB).
		:type in_channels: Int

		:param layer_channels: Number of feature maps in the CNN for each layer.
		:type layer_channels: List of Ints

		:param feature_control_len: Input size to the Feature Attention linear layer.
		:type feature_control_len: Int

		:param spatial_control_len: Input size to the Spatial Attention linear layer.
		:type spatial_control_len: Int

		:param output_shape: Output dimensions of feature maps of last layer.
		:type output_shape: Tuple of Ints

		"""
		# Initial Norm
		#self.batchnorm0 = nn.BatchNorm2d(3)

		# First Layer
		self.conv1 = nn.Conv2d(in_channels,layer_channels[0],3,
								 stride=1,padding=1,dilation=1,groups=1,bias=True)
		self.maxpool1 = nn.MaxPool2d(2,
										stride=None, padding=0, dilation=1, return_indices=False, ceil_mode=False)
		self.batchnorm1 = nn.BatchNorm2d(layer_channels[0])
	

		# Second Layer
		self.conv2 = nn.Conv2d(layer_channels[0],layer_channels[1],3,
								 stride=1,padding=1,dilation=1,groups=1,bias=True)
		self.maxpool2 = nn.MaxPool2d(2,
										stride=None, padding=0, dilation=1, return_indices=False, ceil_mode=False)
		self.batchnorm2 = nn.BatchNorm2d(layer_channels[1])

		# Third Layer
		self.conv3 = nn.Conv2d(layer_channels[1],layer_channels[2],3,
								 stride=1,padding=1,dilation=1,groups=1,bias=True)
		self.maxpool3 = nn.MaxPool2d(2,
										stride=None, padding=0, dilation=1, return_indices=False, ceil_mode=False)
		self.batchnorm3 = nn.BatchNorm2d(layer_channels[2])
		self.feature_attn1 = FeatureAttention(layer_channels[2],feature_control_len)


		# Fourth Layer
		self.conv4 = nn.Conv2d(layer_channels[2],layer_channels[3],3,
								 stride=1,padding=1,dilation=1,groups=1,bias=True)
		self.maxpool4 = nn.MaxPool2d(2,
										stride=None, padding=0, dilation=1, return_indices=False, ceil_mode=False)
		self.batchnorm4 = nn.BatchNorm2d(layer_channels[3])
		self.feature_attn2 = FeatureAttention(layer_channels[3],feature_control_len)
		self.spatial_attn1 = SpatialAttention(output_shape,spatial_control_len)

		# Linear Layer
		self.cnn_linear1 = nn.Linear(layer_channels[3]*output_shape[0]*output_shape[1],128)

	# Semantic Processing
	def SemanticProcessing(self,lstm_input,lstm_hidden,control_len):
		"""
		Defines all layers pertaining to semantic processing.

		:param lstm_input: LSTM input size.
		:type lstm_input: Int

		:param lstm_hidden: LSTM hidden state size.
		:type lstm_hidden: Int

		:param control_len: Input size to the Semantic Attention linear layer.
		:type control_len: Int

		"""
		self.lstm1 = nn.LSTM(lstm_input,lstm_hidden,
								 num_layers=1, batch_first=True,bidirectional=True)

		self.semantic_attn1 = SemanticAttention(lstm_hidden*2,control_len)
		#self.replacement_linear1 = nn.Linear(self.nwords*self.words_embed_length,lstm_hidden*2)

	#Controller and classifier
	def Controller(self,controller_input,controller_hidden,nr_classes):
		"""
		Defines all layers pertaining to the controller.

		:param controller_input: Controller input size.
		:type controller_input: Int

		:param controller_hidden: Controller hidden state size.
		:type controller_hidden: Int

		:param nr_classes: Number of classes in classifier output.
		:type nr_classes: Int

		"""
		self.controller1 = nn.GRU(controller_input, controller_hidden,
											 batch_first=True)
		self.classifier1 = nn.Linear(controller_hidden,nr_classes)

	# VSTM and pointing
	def VisualMemory(self,shape,in_channels,out_channels,n_maps,control_len,nr_pointers):
		"""
		Defines all layers pertaining to the VSTM module.

		:param shape: Shape of VSTM feature maps.
		:type shape: Tuple of Ints

		:param in_channels: Number of feature maps received from CNN.
		:type in_channels: Int

		:param out_channels: Number of feature maps output from VSTM module.
		:type out_channels: Int

		:param n_maps: Number of feature maps stored in the VSTM module.
		:type n_maps: Int

		:param control_len:  Input size to the VSTM gating linear layer.
		:type control_len: Int

		:param nr_pointers:  Number of output classes for the pointer layer.
		:type nr_pointers: Int

		"""
		self.vstm1 = VSTM(shape,in_channels,out_channels,n_maps,control_len)
		self.pointer1 = nn.Linear(128,nr_pointers)
		self.vstm_linear1 = nn.Linear(shape[0]*shape[1]*out_channels,128)

	# Embed vocabulary for all available task families
	def EmbedVocabulary(self,vocabulary_size,words_embed_length):
		"""
		Defines nn.Embedding for embedding of questions into float tensors.

		:param vocabulary_size: Number of unique words possible.
		:type vocabulary_size: Int

		:param words_embed_length: Size of the vectors representing words post embedding.
		:type words_embed_length: Int

		"""
		self.Embedding = nn.Embedding(vocabulary_size,words_embed_length,padding_idx=0)
	

if __name__ == '__main__':
	from miprometheus.utils.param_interface import ParamInterface
	from miprometheus.problems.seq_to_seq.video_text_to_class.cog import COG
	import os
	import torch.optim as optim

	params = ParamInterface()
	tasks = ['AndCompareColor']
	params.add_config_params({'data_folder': os.path.expanduser('~/data/cog'), 'root_folder': ' ', 'set': 'val', 'dataset_type': 'canonical','tasks': tasks})

	# Create problem - task Go
	cog_dataset = COG(params)

	# Set up Dataloader iterator
	from torch.utils.data import DataLoader
	
	dataloader = DataLoader(dataset=cog_dataset, collate_fn=cog_dataset.collate_fn,
		            batch_size=64, shuffle=False, num_workers=8)

	# Get a batch64))
	#print(repr(lstm))
	batch = next(iter(dataloader))

	# Initialize model
	from miprometheus.utils.param_interface import ParamInterface
	params = ParamInterface()
	#params.add_config_params(params_dict)
	model = CogModel(params)

	images = batch['images'].permute(1,0,2,3,4)
	questions = batch['questions']
	logits=model(batch)


	print(logits[0].size())
	print(logits[1].size())


	exit()
	#embedded_questions = model.EmbedQuestions(questions)
	#print(embedded_questions.size())

	# Test forward pass of image64))
	#print(repr(lstm))
	#print(model.forward_img2cnn(images[0]).size())

	# Test forward pass of words
	#embed = model.forward_words2embed(batch['questions'][0][0].split())
	#print(repr(embed))
	#lstm = model.forward_embed2lstm(embed.view(1,128,64))
	#print(repr(lstm))

	# Test full forward pass
	out_pass1, pointing, attention,vstm_state,controller_state = model.forward_full_oneseq(images[0],questions)
	#print(out_pass1)
	#print(out_pass1.size())
	#exit(0)
	out_pass2 = model.forward_full_oneseq(images[1],questions,attention,vstm_state,controller_state)

	
	# Try training!
	# Switch to sequence-major representation to make this much easier.	

	criterion = nn.CrossEntropyLoss()
	optimizer = optim.SGD(model.parameters(), lr=0.001, momentum = 0.9)

	from tensorboardX import SummaryWriter
	writer = SummaryWriter('runs/exp1/')
	#print(images[0].size())
	#print(embedded_questions.size())

	#dummy_images = torch.autograd.Variable(torch.Tensor(64,3,112,112),requires_grad=True)
	#dummy_questions = torch.autograd.Variable(torch.Tensor(64,32,64),requires_grad=True)
	#dummy_out_class,dummy_out_reg,_,_,_ = model.forward_full_oneseq(dummy_images,dummy_questions)
	#writer.add_graph(CogModel().forward_full_oneseq,(dummy_images,dummy_questions))

	for epoch in range(2):
	
		running_loss = 0.0
		for i, data in enumerate(dataloader,0):

			optimizer.zero_grad()
			targets = data['targets_class']
		
			for j, target in enumerate(targets):
				targets[j] = [0 if item=='false' else 1 for item in target]
			targets = torch.LongTensor(targets)
		
			output = model(data)
	
			loss = criterion(output.view(-1,2),targets.view(64*4))
			loss.backward()
			optimizer.step()

			writer.add_scalar('loss',loss.item(),i)
			#writer.add_graph('model',model,(images[0],questions))

			#running_loss += loss.item()
			print(i)
			print('Loss: {}'.format(loss.item()))






	
	
	

